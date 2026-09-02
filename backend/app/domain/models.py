"""SQLAlchemy ORM models.

Structure mirrors the approved spec §4–§8. Design choices:

- ``organization_id`` on every entity (§4) is the multi-tenant boundary: every
  repository query is scoped to exactly one org, and each org is a fully
  separate tenant (its own users, its own Zoho connection, its own decisions —
  see ``ZohoConnection``). There is deliberately no cross-org query surface.
- Queryable fields are real columns; rich/nested sub-objects (metrics, ai,
  evidence_refs, …) are ``JSON`` so the schema stays stable as those evolve.
- Money/quantities are ``Numeric`` (not float) to keep deterministic math exact
  (see known limitations re: SQLite storage affinity).
- Signals are write-once (no update path); every re-run inserts a new row.
- ``source_ref`` / ``evidence_refs`` trace every persisted fact back to the Zoho
  record it came from — raw source is preserved, never mutated.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Read model (projection of Zoho; rebuildable) ─────────────────────────────
class Organization(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    # The zone the business's *day* is measured in. Storage stays UTC; this is
    # what decides which day a timestamp falls on, and it belongs to the tenant
    # for the same reason the currency does — one instance can hold an Indian
    # distributor and a Gulf one. Zoho reports it on the organization record, so
    # a connected company fills it in rather than being asked.
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    # The country whose statutes reach this tenant (ISO 3166-1 alpha-2, e.g.
    # "IN"). It belongs beside timezone and currency for the same reason they
    # do — one instance can hold an Indian distributor and a Gulf one — but it
    # gates rather than formats: ``commercial/jurisdiction.py`` decides from it
    # whether the statutory screens (MSME payment timing, 194Q withholding) may
    # answer at all. NULL means "not established", and unknown is not India —
    # the statutory endpoints refuse rather than assume (CLAUDE.md §1).
    country: Mapped[Optional[str]] = mapped_column(String(2))
    # Which plan this organization is licensed on ("free" | "intelligence" |
    # "platform" — domain.enums.PlanTier). NULL means "not decided here" and
    # resolves to settings.DEFAULT_PLAN, so an existing deployment keeps its
    # behaviour without a backfill. Set by the operator (app/entitlements.py
    # CLI), never by a tenant — an owner who could set their own plan would
    # not have one.
    plan: Mapped[Optional[str]] = mapped_column(String(32))
    # Which plan this organization *asked* for at sign-up. A request, never a
    # licence: ``entitlements.licensed_plan`` reads ``plan`` above and has no
    # idea this column exists, so writing "platform" here grants nothing. It is
    # here because the sign-up form asks the question, and the answer is worth
    # more as a row an operator can list (``python -m app.entitlements
    # requests``) than as an email nobody kept. NULL means never asked.
    requested_plan: Mapped[Optional[str]] = mapped_column(String(32))
    # ``MutableDict``, not a bare ``JSON``, and the wrapper is the whole point.
    # SQLAlchemy notices a change by *assignment*: ``org.config = {...}`` marks
    # the row dirty, ``org.config["k"] = v`` does not, so an in-place write is
    # flushed as nothing at all and the next read returns what was there before.
    # Silently — no error, no warning, a commit that reports success.
    #
    # Every writer today happens to assign a whole new dict (``ai/byok.py``,
    # ``routers/data_status.py``), so there is no live defect here. This is the
    # guardrail rather than the repair: a column read and written by four
    # modules across three layers will eventually meet somebody who reaches for
    # the obvious idiom, and the failure it produces is invisible.
    #
    # The column type is unchanged — this is a Python-side wrapper, so it needs
    # no migration and produces no schema drift.
    #
    # What it does **not** cover, stated because a guardrail believed to be
    # wider than it is, is worse than none: it tracks writes to *this* dict's
    # own keys. ``org.config["a"]["b"] = 1`` mutates a plain nested dict and is
    # still lost — verified, not assumed. Nothing here nests today; a writer who
    # needs to should assign the whole sub-dict back.
    config: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ZohoCredential(Base):
    """One Zoho OAuth grant — an app registration plus one user's refresh token.

    Separate from ``ZohoConnection`` because they are genuinely different
    things, and conflating them was a design error worth naming.

    A Zoho refresh token belongs to a *user*, not to a company. Zoho Books
    passes ``organization_id`` as a request parameter, and ``GET /organizations``
    returns every company that user can see. So one grant already reaches all of
    them: a business with three legal entities under one Zoho login needs one
    credential, not three. Storing the client id, secret and refresh token on
    each connection row forced the same secret to be typed in — and later
    rotated — once per entity, multiplying a one-time job by the number of
    companies for no security benefit at all. The blast radius was identical
    either way, since it was the same secret.

    Sharing a credential does not share data. Each platform organization
    remains a fully separate tenant with its own users, decisions and margins;
    this is only the key used to fetch its rows.

    ``client_secret`` and ``refresh_token`` are encrypted at rest (see
    ``app/crypto.py``) — this table, unlike a ``.env`` file, can end up in a
    database backup or a read replica.

    **The table now holds every connector's grants, not only Zoho's.** The name
    is historical and deliberately kept: renaming a table every imported row's
    provenance convention points at would churn the schema for a cosmetic gain.
    ``connector`` says which system a grant signs into. Zoho rows keep using the
    typed OAuth columns below; every other connector stores its secrets as one
    encrypted JSON document in ``secrets_encrypted`` and its non-secret settings
    (account id, tenant, environment …) in ``config``, because five ERPs have
    five different auth shapes and a typed column per field would grow a
    nullable column per connector per secret. The shape of both documents is
    declared per connector in ``ingestion/erp`` — a credential is only ever
    written through its connector's spec, never free-form.
    """

    __tablename__ = "zoho_credentials"

    credential_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # Which system this grant signs into. The discriminator every credential
    # consumer branches on — through the ``ingestion/erp`` registry, not an if.
    connector: Mapped[str] = mapped_column(String(32), default="zoho",
                                           server_default="zoho")
    # Who may rotate it and decide who else may use it.
    owner_organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(255), default="")

    client_id: Mapped[Optional[str]] = mapped_column(String(255))
    client_secret_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    accounts_base: Mapped[str] = mapped_column(String(255),
                                               default="https://accounts.zoho.in")
    api_base: Mapped[str] = mapped_column(String(255),
                                          default="https://www.zohoapis.in/books/v3")
    # Non-Zoho connectors only. ``secrets_encrypted`` is an encrypted JSON
    # object of the connector's secret fields; ``config`` carries the values
    # that are identifying rather than secret. Zoho rows leave both NULL.
    secrets_encrypted: Mapped[Optional[str]] = mapped_column(String(4096))
    # ``MutableDict`` for the reason ``Organization.config`` gives at length.
    config: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSON))

    # Other platform organizations allowed to connect through this grant.
    # Explicit rather than implicit: a credential reachable by every tenant in
    # the deployment would be a cross-tenant hole, whatever the intent.
    shared_with_organization_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    # When the refresh token was last replaced — the one date that answers
    # "are we overdue a rotation?" without anyone having to remember.
    rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)

    def is_usable_by(self, organization_id: str) -> bool:
        return (organization_id == self.owner_organization_id
                or organization_id in (self.shared_with_organization_ids or []))


class ZohoConnection(Base):
    """One Zoho Books company this organization pulls from.

    **Many per organization.** It used to be one — the platform organization was
    the primary key — which meant a business with three legal entities needed
    three platform tenants to see three sets of books, and no screen could show
    them together. Now an owner adds as many companies as they have and chooses
    which to pull from.

    That has a consequence worth stating rather than burying: rows from every
    connection on an organization land in *that organization's* read model and
    are analysed together. Two companies selling to the same customer produce
    two customer records (different Zoho contact ids), which is right — they are
    different legal relationships — but revenue and margin roll up across them.
    Keep entities apart by giving them separate organizations; put them together
    by giving one organization several connections.

    Pull tuning (pacing, retries, page size, history window) is deliberately NOT
    here: it is shared, global operational behaviour in ``config.py``, not part
    of an account's identity.

    The platform's original default organization has no row here until someone
    explicitly connects it — until then it falls back to the ``ZOHO_*``
    environment variables, so an existing single-tenant deployment keeps working
    unchanged (see ``ingestion/connections.py``).

    **The table now holds every connector's connections.** The name is
    historical, kept for the reason ``ZohoCredential`` states; ``connector``
    says which system a row reads, and ``zoho_organization_id`` is read as "the
    company's id in that system" — a Zoho org id, a Business Central company
    GUID, an Acumatica tenant, a NetSuite account. One shared table rather than
    one per connector is what keeps every query that asks "how many companies
    does this organization have" (the sole-connection adoption guard, the
    multi-company plan gate, the sync-all fan-out, ``origin.Companies``)
    correct without knowing connectors exist.
    """

    __tablename__ = "zoho_connections"
    __table_args__ = (
        # Identity is (connector, company-in-that-system): two systems may
        # legitimately issue the same id string, and one system's company must
        # still not be connected twice.
        UniqueConstraint("organization_id", "connector", "zoho_organization_id",
                         name="uq_zoho_connection_org_company"),
    )

    connection_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                               default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    # Which system this connection reads. The value every imported row's
    # ``connector`` column is stamped from.
    connector: Mapped[str] = mapped_column(String(32), default="zoho",
                                           server_default="zoho")
    # What a person calls this company. The external org id is the identity;
    # this is what makes a list of three connections readable.
    label: Mapped[str] = mapped_column(String(255), default="")
    # Off means "keep the credentials, skip it on a sync-all". Deleting is for
    # connections that are wrong; disabling is for ones that are simply quiet.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    zoho_organization_id: Mapped[str] = mapped_column(String(64))
    credential_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("zoho_credentials.credential_id"), index=True)
    # Non-Zoho connectors only: per-company settings the credential does not
    # carry — a Business Central company GUID is here, its tenant id is on the
    # credential. Shapes are declared per connector in ``ingestion/erp``.
    # ``MutableDict`` for the reason ``Organization.config`` gives at length.
    config: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSON))

    # Legacy inline credentials. Rows created before credentials were separated
    # keep working from these until the migration backfills them; nothing new is
    # ever written here.
    client_id: Mapped[Optional[str]] = mapped_column(String(255))
    client_secret_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    accounts_base: Mapped[str] = mapped_column(String(255), default="https://accounts.zoho.in")
    api_base: Mapped[str] = mapped_column(String(255),
                                          default="https://www.zohoapis.in/books/v3")
    # The currency this connected company keeps its books in, as Zoho reports
    # it. Read on every check rather than filled in once: it is a fact about
    # the company, not a preference somebody set, so a stale value here is
    # worse than none — every figure this connection contributes is denominated
    # in it.
    #
    # It lives on the *connection* and not on the organization because one
    # organization deliberately holds several connected companies and rolls
    # revenue and margin up across them. `Organization.currency` is what those
    # totals are spelled in; this is what each book actually trades in, and the
    # two disagreeing is the condition `_check` warns about and the sync
    # refuses documents on.
    #
    # NULL means nobody has checked this connection yet — not that it agrees.
    base_currency: Mapped[Optional[str]] = mapped_column(String(8))
    # Last time this connection was actually reachable, and what Zoho said.
    # Held per connection because "the org is connected" stops meaning anything
    # once there are three of them and one has a revoked token.
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_check_ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_check_detail: Mapped[Optional[str]] = mapped_column(String(1024))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)

    credential: Mapped[Optional["ZohoCredential"]] = relationship(lazy="joined")


class OAuthState(Base):
    """One in-flight authorization, from the redirect out to the redirect back.

    A row rather than a key in ``Organization.config``, and the difference is
    not tidiness — it is what makes the flow work at all.

    The previous implementation stored these under the organization. That had
    two consequences and both were fatal. The write was
    ``org.config["oauth_states"][hash] = {...}`` on a plain ``JSON`` column, an
    in-place mutation SQLAlchemy does not mark dirty, so it was flushed as
    nothing and every callback failed state validation. And storing it *under*
    the organization meant the callback could only find the state if it already
    knew which organization — so it demanded a bearer token, on an endpoint the
    browser reaches by following Zoho's redirect, which carries no such header.
    The flow 401'd before its handler ran.

    Keyed by the hash, the state is findable by the one value the redirect
    actually carries. That is what lets the callback be public and still know
    exactly whose authorization it is completing.

    **The token itself is never stored.** Only ``sha256`` of it, for the reason
    a password is not stored: this table is readable by anything that reaches
    the database, and a live state token is a usable half of an authorization.

    Single-use, and enforced by a column rather than by deletion —
    ``consumed_at`` set means spent, and a second callback carrying the same
    state is refused rather than silently re-run. Deleting instead would make a
    replay indistinguishable from a state that had expired and been swept.
    """

    __tablename__ = "oauth_states"

    #: ``sha256`` of the state token. Never the token.
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Which platform organization asked. NOT NULL: today the flow starts from
    #: an authenticated owner adding a company to a tenant that already exists.
    #: An install-first signup — where the authorization *creates* the tenant —
    #: is the case that would relax this, and it is a migration when it arrives
    #: rather than a nullable column nothing writes.
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    #: Which system is being authorized. Zoho is the only one today; the column
    #: is here because the table is named for the protocol, not the vendor, and
    #: a second OAuth connector must not need a second table.
    connector: Mapped[str] = mapped_column(String(32), default="zoho",
                                           server_default="zoho")
    #: The data centre chosen at the start. Carried on the row because the
    #: callback needs it to exchange the code, and a redirect cannot be trusted
    #: to hand it back — a ``.com`` user's code redeemed against ``.in`` fails
    #: in a way nobody can diagnose.
    accounts_base: Mapped[str] = mapped_column(String(255))
    api_base: Mapped[str] = mapped_column(String(255))
    #: Set once the callback has spent it. A second use is refused.
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: What the callback hands the browser so an authenticated request can claim
    #: the pending credential. Also a hash, for the reason the state is.
    handoff_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: The credential the exchange created, claimed by the handoff.
    credential_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Indexed because the only query that is not by key is the sweep.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OrganizationMembership(Base):
    """One person's relationship with one organization, and what it lets them do.

    **This is the seam the whole tenant model turns on.** Before it, access was
    two columns on ``users``: an ``organization_id`` and a ``role``. That says a
    person *is* a tenant's employee, which is not what a B2B product means — it
    means a login has been granted access to a customer's workspace, and the
    grant is a thing with its own lifetime, its own author and its own end.
    Everything that reads awkwardly under the old shape reads plainly under
    this one:

    - The first user is not the organization. John founds Acme, John leaves,
      Sarah owns it; three rows change here and nothing about Acme moves. There
      is deliberately no ``owner_user_id`` anywhere on ``organizations`` — the
      owner is whoever currently holds an ACTIVE membership with that role, and
      "currently" is the word an ``organizations`` column could not have said.
    - The same identity can hold two workspaces. The unique key is the *pair*,
      not the user, so a consultant with three clients is three rows rather
      than three logins with three passwords to keep straight.
    - A role becomes answerable per organization. Somebody who owns their own
      company and reads a group company's numbers as a salesperson is two rows;
      under one ``users.role`` they had to be the wider of the two, everywhere.

    ``users.organization_id`` survives this and does **not** mean what it used
    to: it is the *home* organization, the tenant whose row-level-security
    policy the identity row itself lives under and the workspace a sign-in
    lands in. ``memberships.py`` keeps it pointing at an organization the user
    is actually a member of. Authorization never reads it — ``authz`` resolves
    the request's role from the membership named by the session's organization,
    which is what makes a token's ``oid`` a claim that gets checked rather than
    a fact that gets believed.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        # One membership per pair. Not unique on ``user_id``, deliberately —
        # that constraint *is* the one-organization-per-person assumption, and
        # writing it here would rebuild the thing this table exists to remove.
        UniqueConstraint("organization_id", "user_id",
                         name="uq_membership_org_user"),
        # "Who is in this workspace" is the query behind the members screen,
        # every last-owner check and the RLS predicate on ``users``. It is
        # asked on every role change, so it is worth an index rather than a
        # scan of every membership on the platform.
        Index("ix_memberships_org_status", "organization_id", "status"),
        # And the other direction: "which workspaces can this person open",
        # asked at sign-in and on every organization switch.
        Index("ix_memberships_user_status", "user_id", "status"),
    )

    membership_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                               default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), index=True)
    #: ``domain.enums.Role``, and the *only* place a role is decided. A copy
    #: still sits on ``users.role`` for the reason that column's comment gives;
    #: it is written from here and read by nothing that authorizes.
    role: Mapped[str] = mapped_column(String(32))
    #: ``domain.enums.MembershipStatus``. Only ACTIVE grants anything.
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)

    #: Who offered this membership. Null for the founding one — nobody invited
    #: the person who created the organization, and recording them as their own
    #: inviter would be a tidier lie than a null.
    invited_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: Who last changed the role, and when. The most security-relevant edit in
    #: the product; an unattributed one is not worth recording.
    role_changed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    role_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))
    #: Who ended it, and when. Set together with ``status = REMOVED``.
    removed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, onupdate=_now)


class OrganizationSubscription(Base):
    """What this organization is commercially entitled to, and since when.

    One row per organization, created with the organization. It holds the two
    facts that used to live apart and could not be read together: the plan
    (a column on ``organizations``) and the trial (a row in
    ``intelligence_trials``, keyed on the *connected books* rather than on the
    tenant, and started only when somebody connected Zoho).

    **The trial belongs here because it belongs to the customer.** Keyed to the
    books it was unreachable until a connection existed, so a new organization
    had nothing during the days it was most deciding whether to buy; and it
    could not answer "when does this customer's trial end" without knowing
    which books they had connected. Keyed to the organization it starts when
    the customer does, survives every user who comes and goes, and is
    unaffected by a second person joining — a new member reads this row, they
    do not get one of their own.

    ``intelligence_trials`` is *not* deleted and is not a second copy of this.
    It is now only the record that one set of books has claimed a trial, which
    is what stops the same company signing up again under a new address for a
    second free month, and the anchor the attribution baseline is captured
    against. It grants nothing. See ``app/entitlements.py``.

    **Expiry is derived, never written.** ``status`` holds the commercial state
    a person put it in; whether a TRIALING row is still inside its trial is a
    comparison against ``trial_ends_at`` made at read time. A nightly sweep that
    flipped rows to EXPIRED would mean a missed run reads exactly like a
    healthy one, which is the failure mode CLAUDE.md §1 names.
    """

    __tablename__ = "organization_subscriptions"

    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"),
        primary_key=True)
    #: ``domain.enums.PlanTier``: what is being paid for. NULL means nothing is
    #: — the organization is on trial, or past one. Never the trial's own tier:
    #: a trial that wrote "intelligence" here would be indistinguishable from a
    #: subscription the day it lapsed.
    plan: Mapped[Optional[str]] = mapped_column(String(32))
    #: ``domain.enums.SubscriptionStatus``.
    status: Mapped[str] = mapped_column(String(16), default="TRIALING",
                                        index=True)

    trial_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))
    #: When the paid relationship began. Set when a plan is granted and left
    #: alone by later plan moves — the answer to "how long have they been a
    #: customer" must not reset because they changed tier.
    subscription_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))

    #: Why a trial ended early, when one did — today only the duplicate-books
    #: case (``entitlements.claim_books``). Empty for a trial that simply ran
    #: its course, because "expired" is already in ``trial_ends_at`` and a
    #: reason column that restated it would be a second answer to one question.
    trial_ended_reason: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, onupdate=_now)


class IntelligenceTrial(Base):
    """The record that one set of books has claimed its trial — ever.

    **This row grants nothing.** The entitlement moved to
    ``OrganizationSubscription``, where it belongs to the customer rather than
    to whichever company they happened to connect; what stayed here is the
    other half of the same idea, and it is worth keeping for two reasons.

    It is the duplicate-trial check. A platform organization costs nothing to
    create, so a trial tied only to one would reset with every fresh sign-up
    and "reconnect the same company under a new address" would be an
    indefinitely repeatable free month. The connected books are the one thing a
    business cannot mint a new copy of. ``entitlements.claim_books`` writes the
    claim at first connection and, finding one already held by a *different*
    organization, ends the new organization's trial and says so — one simple
    boundary, and deliberately not a fingerprinting scheme.

    It is also the anchor for the attribution baseline: "better" needs a
    "before", and the moment books connect is the only unambiguous one.

    Keyed on ``zoho_organization_id`` with the unique constraint as the
    enforcement, and rows are never deleted — a spent trial is the *record*
    that these books have had theirs, which is exactly what the next attempt
    must find.
    """

    __tablename__ = "intelligence_trials"

    trial_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # The organization that was connected first and received the trial.
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    zoho_organization_id: Mapped[str] = mapped_column(String(64), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanChangeRequest(Base):
    """An owner asking to move plan, and what an operator did about it.

    The row exists because there was no way to ask. ``set_plan`` is an operator
    command with deliberately no API — an owner who could set their own plan
    would not have one — and the honest consequence was that the platform sold
    three tiers and offered no way to buy the upper two. The trial notice said
    so out loud: *"There is no 'Upgrade' button, deliberately… a button that
    opened a checkout nobody built would be worse than no button."*

    This is the button, and it is not a checkout. It records a request and
    **grants nothing**: the plan still moves only through ``set_plan``, which
    still only an operator can reach. Separating the asking from the granting is
    what lets the ask be self-service while the grant stays a decision somebody
    makes.

    Append-only in the way that matters: a request is never edited into a
    different request. Deciding one stamps the outcome on it and leaves what was
    asked for intact, because the argument six months from now is about what
    somebody asked for and when, not about what it later became.
    """

    __tablename__ = "plan_change_requests"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    #: The plan asked for. Stored as its value rather than as a diff from the
    #: current one: an organization's plan can move between the ask and the
    #: decision, and a request that read "one tier up" would then mean something
    #: nobody asked for.
    requested_plan: Mapped[str] = mapped_column(String(32))
    #: What they were on when they asked. Kept for the same reason.
    plan_at_request: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Anything the owner wanted to say — how many companies, when they need it.
    note: Mapped[str] = mapped_column(Text, default="")

    #: REQUESTED until an operator acts. Then APPLIED or DECLINED, with who and
    #: when. Never back to REQUESTED: a second ask is a second row.
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED", index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ContactRequest(Base):
    """A visitor on the public site asking to talk, before there is a tenant.

    The landing page states no price. It never will: what a business pays is
    settled in a conversation, and a page that answered the question in a panel
    was answering it for a reader whose situation it had not asked about. The
    panels now describe what each plan *is* and the section ends in a form, so
    this table is where that form lands.

    **Not tenant-scoped, and that is the whole point.** Every other table in
    this schema carries an ``organization_id`` because every other row belongs
    to a company that has already signed up. The person who fills in this form
    has not: there is no tenant to scope them to, no principal on the request,
    and nothing here is any organization's data. It is the one row in this
    database that exists *before* the relationship does — which is also why it
    is named as the third table in ``test_row_level_security`` rather than
    being given a policy that would have nothing to enforce.

    Append-only in the same way :class:`PlanChangeRequest` is: an enquiry is
    never edited into a different enquiry. Answering one stamps ``handled_at``
    and leaves what was asked intact, because the question six months from now
    is what somebody asked for and when.

    It grants nothing, exactly like the plan-request row above it. Writing here
    creates no account, licenses no plan and sends no mail — it records an ask
    that ``python -m app.contact`` (and ``python -m app.entitlements requests``,
    which lists all three queues together) puts in front of an operator.
    """

    __tablename__ = "contact_requests"

    contact_request_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_uuid)
    #: What they said they are, in their own words. Free text and unvalidated
    #: beyond a length: this is the first thing a stranger types, and a form
    #: that argues with a company name is a form that loses the enquiry.
    company: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    #: Which panel they were reading when they pressed, where they came through
    #: one. A plan *name*, not an order — the same non-promise
    #: ``organizations.requested_plan`` is, and read by nothing that licenses
    #: anything.
    plan: Mapped[str] = mapped_column(String(32), default="")
    #: The ERP they run, where the form asked. It is the single most useful
    #: sentence an operator can have before replying.
    erp: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True)

    #: NEW until somebody replies. Then HANDLED, with who and when. There is no
    #: third state and no way back: a second enquiry is a second row.
    status: Mapped[str] = mapped_column(String(16), default="NEW", index=True)
    handled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    handled_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AIProviderKey(Base):
    """One organization's own API key for one AI provider (BYOK).

    At most one row per (organization, provider): entering a key again is a
    rotation of the row on file, never a second copy — the same rule
    ``ZohoCredential`` learned the hard way. The key is encrypted at rest with
    ``app/crypto.py`` and is write-only through the API: no endpoint returns it,
    only a last-four hint an owner can recognise their own key by.

    Which stored key actually runs is a separate fact — the organization's
    active-provider choice, held in ``Organization.config`` (see ``ai/byok.py``)
    — so a key can be entered, tested and kept without being switched to yet.
    """

    __tablename__ = "ai_provider_keys"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider",
                         name="uq_ai_provider_key_org_provider"),
    )

    key_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    # "anthropic" | "openai" | "gemini" | "openrouter" — validated in
    # ai/byok.py, not here:
    # the domain layer stores facts, it does not make decisions.
    provider: Mapped[str] = mapped_column(String(32))
    api_key_encrypted: Mapped[str] = mapped_column(String(2048))
    # Last characters of the plaintext key, stored so the hint survives without
    # ever decrypting on a read path.
    key_hint: Mapped[str] = mapped_column(String(8), default="")
    # Empty means the provider's configured default model (config.py).
    model: Mapped[str] = mapped_column(String(128), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class CommercialPolicy(Base):
    """One organization's overrides to the commercial thresholds.

    Only the fields ``commercial/policy.py`` marks editable are ever stored, and
    the whole set is validated as a ladder before it is written. Kept as JSON
    rather than columns because the editable set is a product decision that will
    move, and a migration per threshold is a tax on changing one's mind.

    Reproducibility survives editing because ``CommercialThresholds.version`` is
    a content hash: an edited policy has a different version, and every metric
    row, signal and quote snapshot already records the version that produced it.
    Where such a version *dereferences* — the values behind the hash, not merely
    the fact that they differ — is ``threshold_versions`` below, because this
    row holds only today's overrides and only the half of the policy an owner
    can edit.
    """

    __tablename__ = "commercial_policies"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class ThresholdVersion(Base):
    """What a ``ci_…`` or ``th_…`` stamp actually stood for.

    Every computed row in this schema carries a threshold version, and until
    this table existed none of them could be *dereferenced*: the stamp is a
    truncated sha256 of the whole threshold dataclass, so an approval carrying
    ``ci_9f3a…`` proved it had been judged under a policy different from
    today's and could not say which. ``commercial_policies`` cannot answer it
    either — that row is mutable, holds only the owner-editable half, and the
    environment-derived half (window lengths, evidence floors, ``CI_RECENT_DAYS``)
    never passed through it at all. This table holds the exact bytes that were
    hashed, written in the same transaction as the first row ever stamped with
    them, so the answer is verifiable by re-hashing rather than trusted.

    **The primary key is ``(organization_id, version)``, not ``version``.**
    A shared, version-only key would store each blob once and is the shape most
    readers reach for first; it was rejected because ``first_seen_at`` only
    means anything per organization. The epoch — this tenant's earliest sighting
    of a stamp of a given kind — is what separates "stamped before the registry
    existed, expected and finite" from "a stamp reached a row without being
    recorded, which is a bug in the recording path". Under a shared key an
    organization onboarded last week would inherit another tenant's six-month-old
    epoch and report its own genuine recording gaps as expected history, which
    is exactly the benign default §1 forbids. The stamp is already a per-org
    artefact in any case: ``policy._in_org_locale`` folds the organization's
    currency and timezone into the hash before it is taken.

    The cost is real and is recorded here rather than discovered later: two
    tenants on identical policy store the same blob twice, every resolution
    needs an organization to resolve *within*, and "which tenants run this
    policy" becomes a scan instead of a lookup. Narrow-first is the reversible
    direction — a per-org table can be widened to a shared one later, while a
    shared one cannot be split back into per-org epochs that were never recorded.

    Nothing is ever updated here. A row is written once, by whichever recorder
    saw the version first, and re-recording is a no-op.
    """

    __tablename__ = "threshold_versions"

    #: No ``ForeignKey`` to ``organizations``, deliberately. ``CommercialPolicy``
    #: directly above — the closest sibling and the same tenant-scoped shape —
    #: has none either. This is bookkeeping that rides inside somebody else's
    #: business transaction, and an FK here could make a quote save fail on a
    #: constraint about a registry row, which inverts the priority: the stamped
    #: row is the thing that must commit.
    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: The stamp as it appears on a computed row: ``ci_`` or ``th_`` plus ten
    #: hex characters. 64 rather than the 13 a value needs, matching the widest
    #: existing stamp column (``BusinessState.thresholds_version``) so a join
    #: never compares two different widths.
    version: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: "commercial" | "signal". Derivable from the ``ci_``/``th_`` prefix, and
    #: stored anyway because both real queries — the per-kind epoch and the
    #: per-kind history listing — filter on it, and a ``LIKE 'ci_%'`` is both
    #: unindexable in the general case and a place where ``_`` is a wildcard.
    #: Do not delete it as redundant; it is denormalised on purpose.
    kind: Mapped[str] = mapped_column(String(16))

    #: The exact bytes that were hashed: ``json.dumps(asdict(th), sort_keys=True)``.
    #: ``Text`` rather than ``JSON`` because the row must be self-verifying —
    #: re-hashing has to see the same byte sequence the stamp was taken over,
    #: and a round-trip through SQLAlchemy's JSON encoder is free to reorder
    #: keys or respell a float and would break the one check that makes this
    #: table trustworthy.
    values_json: Mapped[str] = mapped_column(Text)

    #: The untruncated sha256 of ``values_json``. Not derivable from ``version``,
    #: which keeps only the first ten hex characters — 40 bits, which is a
    #: birthday collision somewhere around a million distinct policies per
    #: tenant. Storing the full digest is what makes such a collision *detectable*
    #: at resolve time rather than silently served as the wrong policy.
    content_digest: Mapped[str] = mapped_column(String(64), default="")

    #: When this organization was first *seen* using this version. A sighting,
    #: not an effective date: the recorders fire when a stamp is loaded, saved
    #: or flushed, so a policy in force for months before the registry shipped
    #: is first seen on the day it ships. Renaming this ``effective_from`` would
    #: manufacture a timeline out of a sighting log and invite readers to date
    #: a decision from it.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    default=_now)

    #: Which recorder got here first: "stamp" (a flush carrying the version),
    #: "boot" (the startup backfill of the current policy), "edit"
    #: (``policy.save_for_org``). Diagnostic only — it says how a version came
    #: to be known, never how authoritative it is.
    first_seen_via: Mapped[str] = mapped_column(String(16), default="stamp")

    __table_args__ = (
        # The primary key already serves resolution. This one serves the other
        # two reads: the per-kind epoch (``MIN(first_seen_at)``) and the history
        # listing for one tenant.
        Index("ix_threshold_version_org_kind_seen",
              "organization_id", "kind", "first_seen_at"),
    )


class User(Base):
    """A person who can sign in. Identity only — access is a membership.

    What this row answers is "who is this?", and after
    ``OrganizationMembership`` landed that is *all* it answers. What they may
    open, and as what, is one or more rows in that table, resolved per request
    against the organization the session is acting for.

    Two columns below survived the split and no longer mean what their names
    suggest. Both are documented at the column rather than here, because the
    place somebody reads a column is next to it.
    """

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: The **home** organization: the tenant this identity row is filed under,
    #: not the limit of what its holder can reach.
    #:
    #: It is still a real column with real work to do rather than a leftover.
    #: The row-level-security policy on ``users`` is written against it, sign-in
    #: reads it through ``app_login_lookup`` to know which tenant to announce
    #: before any query runs, and a session opened without an organization named
    #: lands here. ``memberships.py`` keeps it pointing at an organization the
    #: user actually holds an ACTIVE membership in, and repoints it when that
    #: membership ends.
    #:
    #: **Nothing authorizes on it.** ``authz.load_principal`` resolves the
    #: request's organization from the session and its role from the membership
    #: for that pair — so a user whose home is Acme and who also belongs to Beta
    #: gets Beta's role in a Beta session, and a token naming an organization
    #: they hold no membership in is refused rather than believed.
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    # email is an auth affordance beyond the spec's minimal User; nullable/unique.
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    #: **Deprecated: the role in the *home* organization, mirrored.**
    #:
    #: The authority is ``organization_memberships.role``. This column is kept
    #: for one release so that a rollback to the previous deployment finds the
    #: rows it expects rather than an empty column, and because the audit log
    #: stamps an actor's role on paths that predate memberships. It is written
    #: in exactly one place — ``memberships._mirror_home_role`` — and read by
    #: nothing that decides anything; ``test_the_legacy_user_role_mirrors_the_
    #: home_membership`` pins that they agree, so the mirror cannot drift
    #: silently while it is still here.
    #:
    #: Two columns holding one fact is the responsibility duplication CLAUDE.md
    #: §2 is about, and this is that defect with a stated expiry rather than an
    #: exception to the rule. Dropping it is a migration of its own, after the
    #: new model has run.
    role: Mapped[str] = mapped_column(String(32))
    #: Whether this identity can sign in **at all**, on any organization. Not
    #: the same question as whether a membership is ACTIVE: deactivating John
    #: closes his login everywhere, removing his Acme membership closes one
    #: door. Both are needed and neither implies the other.
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── credentials ──────────────────────────────────────────────────────────
    # Nullable, and a null means "cannot sign in" — never "any password works",
    # which is what the login endpoint previously did and which made the three
    # roles a display preference rather than a boundary.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    #: When this account's password last changed. Tokens carry an `iat`, so this
    #: is what makes a credential change retire the sessions that were opened
    #: with the old one — otherwise a token minted before the change kept working
    #: indefinitely, which is the one thing a password change is for after a
    #: suspected compromise. Null on an account whose password has never changed.
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Who created this account, and who last changed its role. Role changes are
    # the most security-relevant edit in the product; an unattributed one is not
    # worth recording.
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    role_changed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    role_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Login failure throttling: count resets on successful auth, locks account
    # after 5 consecutive failures with exponential backoff (30s, 60s, 120s, 240s…).
    login_failures_count: Mapped[int] = mapped_column(Integer, default=0)
    login_failures_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSession(Base):
    """One sign-in, as a row — so it can be ended.

    The token used to be the whole session: an HMAC over ``{uid, oid, iat}`` and
    nothing else. That made signing out a client-side fiction. Clearing the
    browser's copy left the token cryptographically valid for its full 30 days,
    so a token captured from a shared machine outlived the "sign out" the person
    clicked, and there was no way to end one session without changing the
    password and ending all of them.

    So the token now carries a ``sid`` naming a row here, and
    ``authz.load_principal`` reads it on every request. Revoking is a write, not
    a hope. The cost is one indexed lookup per request, which is not a new class
    of cost: the same function already loads the ``User`` row to resolve role and
    active status, and for the same reason — authority belongs in the database,
    where it can be withdrawn, never in a bearer's copy of it.

    ``revoked_at`` is set, never deleted. A signed-out session stays as evidence
    that it existed and when it ended; ``authz.purge_expired_sessions`` is what
    eventually reclaims the rows, long after they could matter.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        # The session list and the revoke-all sweep both ask "live sessions for
        # this user", which is this index. `revoked_at` is not in it on purpose:
        # it is null for exactly the rows those queries want, and a null-heavy
        # column adds nothing to the lookup.
        Index("ix_user_sessions_user_live", "user_id", "revoked_at"),
    )

    #: Opaque and random, never derived from the user — it travels in a token
    #: and, before the cookie, in `localStorage`. A guessable id would let a
    #: caller name a session that is not theirs on the revoke endpoint; the
    #: endpoint checks ownership anyway, and this makes the check redundant
    #: rather than load-bearing.
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), index=True)
    #: Denormalised from the user so a revocation sweep never has to join. The
    #: token carries an org too, and `load_principal` rejects a mismatch.
    organization_id: Mapped[str] = mapped_column(String(64), index=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True)
    #: Advanced lazily — see `authz.LAST_SEEN_RESOLUTION_SECONDS`. Writing it on
    #: every request would put a write in front of every read, which on SQLite
    #: means a write lock in front of every read.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    #: What the person should recognise in the session list — "Chrome on
    #: Windows", roughly. Truncated, and deliberately the only thing recorded
    #: about the client: an IP address would be a second, more sensitive
    #: identifier for no gain over "is this device mine?".
    user_agent: Mapped[Optional[str]] = mapped_column(String(256))


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id",
                         "external_id", name="uq_customer_source"),
    )

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # ── where this record came from ──────────────────────────────────────────
    #
    # An imported record's identity is (connector, connection, the id that
    # system gave it) — never its name. Two connected companies can both hold
    # "ABC Industries", and they are two different customers who happen to
    # share a name; one connected company's id space says nothing about
    # another's, so an external id alone is not unique either.
    #
    # This used to be keyed on (organization, external_id), which was correct
    # only by accident: Zoho's contact ids happen to be globally unique, so two
    # Zoho companies never collided. The first non-Zoho connector breaks that —
    # Tally numbers its ledgers from 1 per company — and the failure is silent,
    # two different customers upserting onto one row.
    #
    # Nullable because rows imported before this existed cannot be attributed
    # after the fact, and guessing which company they came from would be
    # inventing provenance. NULL renders as "source not recorded", which is the
    # true statement.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    assigned_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # Zoho's salesperson on this account's most recent invoice, kept as Zoho's own
    # id. Held separately from assigned_user_id so ownership survives a resumed
    # pull that never re-reads those invoices, and so the mapping stays reversible.
    source_owner_id: Mapped[Optional[str]] = mapped_column(String(64))
    source_owner_at: Mapped[Optional[date]] = mapped_column(Date)
    first_seen: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # Whether a third-party incentive may be paid on this account: "PRIVATE",
    # "RESTRICTED" (PSU, government, defence supply chain) or NULL for one
    # nobody has classified yet. NULL is treated exactly like RESTRICTED — see
    # ``commercial.incentive.may_pay_third_party`` for why it fails closed and
    # why nothing infers this from the customer's name.
    incentive_eligibility: Mapped[Optional[str]] = mapped_column(String(16))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id",
                         "external_id", name="uq_product_source"),
    )

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # See ``Customer`` for why the source is part of the key, not decoration.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    uom: Mapped[Optional[str]] = mapped_column(String(32))
    hsn: Mapped[Optional[str]] = mapped_column(String(32))
    #: The catalogue's own category, exactly as Zoho words it — "Cutting Tools",
    #: "Coolant", whatever somebody typed. Stored raw and interpreted at read
    #: time by ``commercial/categories.py``, not normalised on the way in: the
    #: mapping from these words to a line of the business is policy, it is
    #: versioned, and a value rewritten at sync time could never be re-read
    #: under a corrected map without a full re-sync.
    category: Mapped[Optional[str]] = mapped_column(String(128))
    #: Who makes this, as the item master words it — "KENNAMETAL INDIA
    #: LIMITED", "YG1", "NOGA". Stored raw for the same reason ``category`` is:
    #: mapping a maker onto a principal is policy, and a value rewritten at sync
    #: time could never be re-read under a corrected map.
    #:
    #: Named for the Zoho field that fills it. Items also carry a separate
    #: ``brand`` field, which these books leave empty — ``zoho_client`` reads it
    #: only as a last resort behind ``manufacturer``, so one column holds one
    #: answer and nothing downstream has to know there were two candidates.
    #:
    #: **This is not the vendor and must never be used as one.** The
    #: manufacturer is whose product this is; the vendor on a bill is who we
    #: actually paid. For an authorised distributor they usually coincide, which
    #: is exactly what makes the divergences worth seeing — stock bought from
    #: another distributor to cover a shortfall, a competing make filled through
    #: a trader, an import through an intermediary. ``commercial/principals.py``
    #: chains the two for *sales* attribution and documents where it must not
    #: be chained.
    manufacturer: Mapped[Optional[str]] = mapped_column(String(128))

    #: The taxonomy a person in this business maintains, in that person's own
    #: words. ``source_item_type`` is the tool class — Insert, Drill, Endmill, Tap,
    #: Toolbit, Tool Holder, Measuring Instrument. ``source_item_category`` is the
    #: operation — Milling, Holemaking, Threading, Turning, Toolholding,
    #: Grooving & Parting, General.
    #:
    #: **Neither of these is ``category``**, which is the ERP's own catalogue
    #: category (Zoho's Inventory categories, ``ItemClass`` on Acumatica, a
    #: product line on Sage) and is set on none of the 800 items on the live
    #: SLS master. These two are set on every item sampled there. That is the
    #: whole reason they exist as columns: the column the platform already read
    #: is empty and the one it did not read is full.
    #:
    #: Raw and interpreted at read time, for the same reason ``category`` and
    #: ``manufacturer`` are — the map onto anything the platform reasons with is
    #: versioned policy, and a value rewritten at sync time could never be
    #: re-read under a corrected map without a full re-sync.
    #:
    #: **Evidence about an item, not a verified fact about it.** The live master
    #: files "HSS Taper Shank Reamer Dia 10mm" as Tap / Threading; a reamer is
    #: neither. Rank with it, explain with it, prefer with it — but a
    #: compatibility decision that *gates* on it is trusting a typo, and the
    #: item's decoded designation is the fact to gate on.
    #:
    #: NULL means the source keeps no such taxonomy, or keeps it and left this
    #: item blank. Those are the same value on purpose: both mean nobody said,
    #: and neither may be filled in from the item's name.
    source_item_type: Mapped[Optional[str]] = mapped_column(String(128))
    source_item_category: Mapped[Optional[str]] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    #: The decoded manufacturer catalogue record this item **is** — pie-parser's
    #: ``record_id`` (a Kennametal MM#), or NULL.
    #:
    #: Link, never merge, for the reason ``identity/`` gives: the master row and
    #: the catalogue row stay separate objects with a pointer between them, so a
    #: wrong link is undone by clearing a column rather than by reconstructing a
    #: record that a merge destroyed.
    #:
    #: **NULL means unlinked and must never be read as anything else.** It is
    #: the value for an item nobody has matched, for a principal this pack does
    #: not cover, and for a sync that ran with the catalogue absent. Measured
    #: against the live master, ~9% of items link — so NULL is the common case,
    #: and a caller that reads it as "no such product" rather than "not known
    #: here" will be wrong about the other 91%.
    #: See ``docs/concepts/01-application-engineering.md``.
    pie_record_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    #: How the link was established. Only ``SKU_EXACT`` today, and the column
    #: exists so a second method can never be mistaken for the first: an exact
    #: catalogue-number hit and a human confirmation are different evidence, and
    #: a reader must be able to tell which one a row rests on.
    pie_link_method: Mapped[Optional[str]] = mapped_column(String(32))

    #: The ruleset checksum of the catalogue that produced the link — the stamp
    #: ``QuoteDecision.catalog_version`` also carries, here for the reason
    #: ``thresholds_version`` is on a computed row: it says *which* catalogue
    #: judged this, so a link written under a superseded corpus is identifiable
    #: rather than merely stale.
    pie_catalog_version: Mapped[Optional[str]] = mapped_column(String(128))

    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class VendorTarget(Base):
    """What a principal expects this distributor to do, in a period.

    An authorised distributor does not choose its own numbers — Kennametal,
    Sandvik and the rest set them, per period, and the year is run against them.
    Nothing in Zoho holds a target and nothing derives one, so this is the one
    table in the platform whose contents are *typed rather than synced*. It is
    not derived, it is not rebuildable from a re-sync, and it must survive one.

    ``basis`` is not decoration. A principal's target is usually on what you
    **buy** from them; some are on what you **sell** of their product. Those are
    different numbers against different actuals, and a single "target" column
    would quietly compare one to the other — which is the kind of error nobody
    catches until a quarter closes wrong.

    Periods are stored as explicit start and end dates rather than as a quarter
    label, because principals do not agree on a financial year: an Indian
    principal's Q1 is April to June and a European parent's is January to March.
    A label would have to be interpreted; two dates cannot be misread.
    """

    __tablename__ = "vendor_targets"
    __table_args__ = (
        UniqueConstraint("organization_id", "vendor_id", "period_start",
                         "period_end", "basis", name="uq_vendor_target_period"),
        Index("ix_vendor_target_org_period", "organization_id", "period_start"),
    )

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                           default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    vendor_id: Mapped[str] = mapped_column(String(64),
                                           ForeignKey("vendors.vendor_id"),
                                           index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    #: ``PURCHASE`` — what we buy from them. ``SALES`` — what we sell of theirs.
    basis: Mapped[str] = mapped_column(String(16), default="PURCHASE")
    amount: Mapped[Any] = mapped_column(Numeric(18, 4))
    #: Who typed it, and what they were told. A target nobody can source is one
    #: nobody argues with when it is missed.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class VendorSchemeSlab(Base):
    """One rung of a principal's rebate: buy this much, earn this rate.

    A target is only half of what a principal puts in writing. The other half is
    the scheme — "and if you do, you earn 2.5%" — which on this book is two to
    three percent of purchases and the difference between a good year and an
    ordinary one. Nothing in Zoho holds it either, so like ``VendorTarget`` this
    is typed rather than synced, it is the only copy, and it must survive a
    complete re-sync.

    **The scheme is the set of slabs, and there is no header row.** The common
    shape here is not one number — it is 2% at forty lakh and 3% at sixty — so
    slabs are the model and a flat percentage is the one-slab case. A parent
    table would carry a note and a kind, both of which either duplicate the
    target's or invite a "flat" scheme that computes down a different path from
    the single-slab scheme saying the same thing.

    **Hung off the target, not off the vendor.** A rebate with no number to hit
    is not a scheme, and the vendor, the period and the basis are already stated
    once on ``VendorTarget``. Repeating them here would let the two disagree
    about which quarter a scheme belongs to, and the one that is wrong at year
    end is ours. Deleting a target takes its slabs with it — see the router;
    a slab with no target is unreachable rather than merely untidy.

    ``rate`` is a ratio (``0.025``), never a percentage, matching how margin is
    held everywhere in this platform. ``threshold`` is money, in the same
    currency and at the same grain as ``VendorTarget.amount``.
    ``commercial/insight/schemes.py`` owns what a valid set of them means:
    ascending thresholds, rising rates, and a ceiling that catches ``2.5``
    entered where the field wanted ``0.025``.
    """

    __tablename__ = "vendor_scheme_slabs"
    __table_args__ = (
        UniqueConstraint("target_id", "threshold_amount",
                         name="uq_vendor_scheme_slab_threshold"),
    )

    slab_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                         default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64),
                                           ForeignKey("vendor_targets.target_id"),
                                           index=True)
    #: What has to be bought (or sold, per the target's basis) to reach this rung.
    threshold_amount: Mapped[Any] = mapped_column(Numeric(18, 4))
    #: A ratio. ``0.025`` is two and a half percent, paid on the whole amount
    #: rather than on the excess above the threshold — which is how every scheme
    #: on this book settles, and is stated in ``insight/schemes.py``.
    rate: Mapped[Any] = mapped_column(Numeric(9, 6))
    #: Who typed it, and what they were told. A rebate nobody can source is one
    #: nobody can argue for when the principal's statement says otherwise.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class VendorPaymentTerm(Base):
    """What we actually agreed to pay a supplier in — not what Zoho could express.

    Zoho's payment terms are a fixed list, so a bill raised under a real
    agreement of "net 37" or "45 days from month end" gets filed under the
    nearest thing on the dropdown. Every due date derived from it is then wrong
    by days, in a direction nobody chose, and the cash projection places money
    on those dates. This table is the agreement itself.

    A separate table rather than a column on ``Vendor``, and for the same reason
    ``ItemCategoryOverride`` is separate from ``Product``: vendors are *derived*
    — ``upsert_vendor`` rewrites ``payment_terms_days`` from the payload on
    every sync — so a negotiated term stored there would survive exactly until
    the next pull. This is typed, it is the only copy, and it must survive a
    complete re-sync.

    **Zoho's value is never overwritten.** ``Vendor.payment_terms_days`` keeps
    saying what the ERP says; this says what was agreed. Both are shown, because
    the difference between them is the thing worth seeing — and because a
    schedule that quietly disagreed with Zoho with no way to see why is a
    schedule nobody can reconcile.

    ``basis`` is not decoration, the same way it is not on ``VendorTarget``.
    "45 days" and "45 days from the end of the month" are up to a month apart on
    the same bill, and a single day count would silently treat one as the other.
    """

    __tablename__ = "vendor_payment_terms"
    __table_args__ = (
        UniqueConstraint("organization_id", "vendor_id",
                         name="uq_vendor_payment_term_vendor"),
    )

    vendor_payment_term_id: Mapped[str] = mapped_column(String(64),
                                                        primary_key=True,
                                                        default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    vendor_id: Mapped[str] = mapped_column(String(64),
                                           ForeignKey("vendors.vendor_id"),
                                           index=True)
    #: Days. Counted from the bill date under ``NET``, and from the last day of
    #: the bill's month under ``END_OF_MONTH``.
    days: Mapped[int] = mapped_column(Integer)
    #: ``NET`` or ``END_OF_MONTH``. See ``commercial/insight/terms.py``, which
    #: owns what each one means as a date.
    basis: Mapped[str] = mapped_column(String(16), default="NET")
    #: Who typed it, and what they were told. A term nobody can source is one
    #: nobody can defend when a supplier disputes it.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class CustomerCreditLimit(Base):
    """How much credit this customer was given. The line the balance is read
    against.

    The platform could already say that an account takes 69 days to pay and
    that another is late but predictable. It could not say that either of them
    is past the limit somebody gave them, because no limit existed anywhere —
    which is the difference between an observation ("this customer is slow")
    and a decision ("do not ship this order").

    A separate table rather than a column on ``Customer``, for exactly the
    reason ``VendorPaymentTerm`` is separate from ``Vendor``: customers are
    *derived*. ``upsert_customer`` rewrites the synced fields from the payload
    on every pull and a complete re-sync rebuilds every row from nothing (§4),
    so a limit stored there would survive until the next sync and no longer.
    This is typed, it is the only copy, and it must survive a re-sync. There is
    a test that runs a second sync and checks it is still here.

    **Absent is not zero, and it is certainly not unlimited.** No row means "no
    limit recorded", which is the state most of this book is in; a row holding
    ``0`` means somebody decided this account ships against cash. Those are
    different instructions to a person, so they are different states — and a
    withdrawal deletes the row rather than writing a zero. Same distinction the
    payment terms make between an agreed term and the ERP's guess.

    Zoho holds no credit limit on a contact in this book, so unlike a payment
    term there is no ERP value beside this one to disagree with. If a connector
    ever supplies one it belongs on ``Customer`` as a synced field, and this
    stays the agreement.
    """

    __tablename__ = "customer_credit_limits"
    __table_args__ = (
        UniqueConstraint("organization_id", "customer_id",
                         name="uq_customer_credit_limit_customer"),
    )

    credit_limit_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                 default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64),
                                             ForeignKey("customers.customer_id"),
                                             index=True)
    #: Money, so ``Numeric`` — never a float. The currency is the
    #: organization's, the same one every other figure on the screen is in.
    amount: Mapped[Any] = mapped_column(Numeric(18, 4))
    #: Who set it, and what they were told. A limit nobody can source is one
    #: nobody can defend when a salesperson asks why an order is being held.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class CustomerAccountOwner(Base):
    """Who owns this account — as a person decided, not as Zoho happened to file it.

    ``Customer.assigned_user_id`` already exists and is *derived*:
    ``_sync_assignments`` sets it from the salesperson on the account's most
    recent invoice, and only when that person's Zoho email matches a platform
    user exactly. That covers accounts Zoho already knows about and nothing
    else — a new account nobody has invoiced yet, an account whose salesperson
    has no Zoho login, or a book handed over to somebody else last week are all
    unowned or owned by the wrong person, and the next sync would put back
    whatever was typed over it.

    So this table is the assignment somebody made, and the synced field keeps
    saying what the ERP implies. Same discipline as ``VendorPaymentTerm``:
    **the derived value is never overwritten**, both are available, and
    ``commercial/ownership.effective`` is the one place that says which wins.
    Withdrawing an assignment deletes the row and falls back to Zoho's, rather
    than freezing today's answer against a book that keeps moving.

    Ownership is what makes a per-person collections list possible at all —
    "Rahul's overdue accounts" is not a query anything could answer while every
    account belonged to nobody.
    """

    __tablename__ = "customer_account_owners"
    __table_args__ = (
        UniqueConstraint("organization_id", "customer_id",
                         name="uq_customer_account_owner_customer"),
    )

    account_owner_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                  default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64),
                                             ForeignKey("customers.customer_id"),
                                             index=True)
    #: The platform user who owns the relationship. Indexed because "everything
    #: in my book" is the query this table exists to make answerable.
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"),
                                         index=True)
    #: Who assigned it, and why. A handover nobody can source is one nobody can
    #: argue with when a commission is calculated from it.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class VendorMsmeStatus(Base):
    """Whether a supplier is protected by the MSME 45-day rule, and on what evidence.

    Section 43B(h) disallows a deduction for anything owed to a registered
    micro or small enterprise beyond the section 15 limit. Every other input to
    that test is already here — bill dates, payment dates, balances — so this
    single field is the whole distance between the platform holding the answer
    and being unable to ask the question.

    A separate table rather than a column on ``Vendor``, for the reason
    ``VendorPaymentTerm`` gives: ``upsert_vendor`` rewrites the vendor row from
    the payload on every sync, so a status stored there would survive exactly
    until the next pull. This is typed by a person, it is the only copy, and it
    must outlive a complete re-sync.

    **Nothing here is ever inferred.** Not from turnover in our own books, not
    from how much we buy, not from the supplier's name. ``UNKNOWN`` is the
    default and produces a data-gap row on the watchlist — never a silent pass.
    That is the same rule ``Customer.incentive_eligibility`` follows and the
    same rule the cost placeholders follow, and it is here for the same reason:
    a benign default on a missing fact reads as good news.

    ``written_agreement`` is nullable on purpose, and the three states are
    genuinely different. Absent a written agreement section 15 allows **15**
    days, not 45; a written one may extend that to a maximum of 45. ``None``
    means nobody has established which, so the conservative 15 applies and the
    row says the basis was a default. Zoho's ``payment_terms`` is emphatically
    not evidence of a written agreement — it is a fixed dropdown, as
    ``commercial/insight/terms.py`` explains at length — so reading a Zoho term
    of 45 as an agreement would understate exposure on precisely the suppliers
    with no contract, who are the ones this rule exists to protect.
    """

    __tablename__ = "vendor_msme_statuses"
    __table_args__ = (
        UniqueConstraint("organization_id", "vendor_id",
                         name="uq_vendor_msme_status_vendor"),
    )

    vendor_msme_status_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                       default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    vendor_id: Mapped[str] = mapped_column(String(64),
                                           ForeignKey("vendors.vendor_id"),
                                           index=True)
    #: ``MsmeClassification``. MICRO and SMALL are in scope; MEDIUM is not.
    classification: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    #: ``EnterpriseActivity``. A registered TRADER is out of scope — see the
    #: enum, where the reason is worth reading before anyone "simplifies" this
    #: field away.
    enterprise_activity: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    udyam_number: Mapped[Optional[str]] = mapped_column(String(32))
    #: True / False / None, and None is not False. See the class docstring.
    written_agreement: Mapped[Optional[bool]] = mapped_column(Boolean)
    #: Only meaningful when ``written_agreement`` is True, and capped at the
    #: statutory maximum when the deadline is computed rather than on write —
    #: what was agreed and what the law allows are two different facts, and
    #: overwriting the first with the second loses the disagreement.
    agreed_days: Mapped[Optional[int]] = mapped_column(Integer)
    #: ``MsmeEvidence``.
    evidence: Mapped[str] = mapped_column(String(24), default="NONE")
    #: When this status began to be true. A supplier can cross out of micro or
    #: small, and a bill is judged against the status in force on its own date.
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class ItemCategoryOverride(Base):
    """What a person said an item's line is, when the catalogue could not say.

    A separate table rather than a column on ``Product``, and that is the whole
    point of it. Products are *derived*: Zoho is the system of record and a full
    re-sync rebuilds every product row from nothing (§4). A mapping somebody sat
    down and typed is not derived — it is the only copy — and putting it on a
    derived row means the next complete re-sync silently deletes an afternoon of
    somebody's work.

    Keyed by ``product_id`` because that is what the rest of the platform joins
    on, and it survives a re-sync: ``upsert_product`` matches on the source key
    and keeps the row it already had.
    """

    __tablename__ = "item_category_overrides"
    __table_args__ = (
        UniqueConstraint("organization_id", "product_id",
                         name="uq_item_category_override"),
    )

    override_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                             default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64),
                                            ForeignKey("products.product_id"),
                                            index=True)
    #: One of ``commercial.categories.ORDER``. Validated at the router rather
    #: than by an enum column, so adding a line is a code change and not a
    #: migration against every historical row.
    category: Mapped[str] = mapped_column(String(48))
    #: Who decided, and when. An override is a judgement, and a judgement with
    #: no name on it is one nobody can ask about.
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class TenderResult(Base):
    """A published tender, what it asked for, and what we won of it.

    The one place this platform can *measure* share of wallet rather than
    estimate it. A government or PSU tender states the quantity and value it is
    buying, in a document anybody can read, and the award says who supplied it.
    So for that customer, over that tender, ``won ÷ tendered`` is arithmetic
    over two published facts — not an inference about spend the platform cannot
    see, which is what ``insight/dependency.py`` correctly refuses to make.

    **Scoped to what was tendered, and named for it.** This says nothing about
    the same customer's off-tender buying — the spares, the consumables, the
    repeat orders that never reach a bid. A customer at 60% of their tendered
    tooling may be at 5% of their total, and a field called "share of wallet"
    holding this number would be a lie of scope. ``insight/wallet.py`` reports
    it as ``MEASURED_TENDER`` for that reason.

    **Typed in, not derived.** Nothing syncs this: tender portals are not a
    connector and the award notice is a PDF. It is therefore a *record of what
    somebody read*, and it carries the reference and the source so a figure
    built on it can be checked back to the document. A row with no reference is
    refused at the router — a measured share whose measurement cannot be looked
    up is no better than a guess.

    Append-only in spirit and mutable in fact: an award is learned after the
    bid, so ``won_value`` and ``awarded_on`` fill in later. What must never
    change is ``tendered_value`` — that is what the published document said, and
    editing it to reconcile a share would be editing the evidence.
    """

    __tablename__ = "tender_results"
    __table_args__ = (
        UniqueConstraint("organization_id", "tender_ref",
                         name="uq_tender_result_org_ref"),
        Index("ix_tender_results_org_customer", "organization_id", "customer_id"),
    )

    tender_result_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                  default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The bid number as the portal writes it — GeM bid id, NIT number, tender
    #: id. Unique per organization: the same bid recorded twice would count its
    #: value twice in a denominator, which inflates a share silently.
    tender_ref: Mapped[str] = mapped_column(String(128), index=True)
    #: Who is buying. Resolved where possible; the ref is kept regardless,
    #: because a tender from a customer not yet on the book is still a real
    #: observation and dropping it would lose exactly the accounts with the
    #: least trade behind them.
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    customer_ref: Mapped[str] = mapped_column(String(255), default="")

    #: What the published document asked for. Never edited to reconcile a
    #: share — this is the evidence, not a working figure.
    tendered_value: Mapped[Any] = mapped_column(Numeric(18, 2))
    #: What we were awarded. NULL until the award is known, which is not zero:
    #: an undecided tender excluded from the denominator is honest, and one
    #: counted as a loss is a share understated by however many bids are open.
    won_value: Mapped[Optional[Any]] = mapped_column(Numeric(18, 2))

    #: Which lines of the business this tender covers, so a share can say what
    #: it is a share *of*. One of ``commercial.categories.ORDER`` per entry.
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)

    closed_on: Mapped[date] = mapped_column(Date, index=True)
    awarded_on: Mapped[Optional[date]] = mapped_column(Date)

    #: Where this was read — a portal URL, a document name, an ingested
    #: document id. Required at the router: a measured share whose measurement
    #: cannot be looked up is not measured.
    source: Mapped[str] = mapped_column(String(512), default="")
    recorded_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class SalesTxn(Base):
    """Invoice-line grain (§4)."""

    __tablename__ = "sales_txns"
    # The two composite indexes existed in the database for a long time without
    # being declared here, which meant every ``--autogenerate`` wanted to DROP
    # them: an index nobody declared is an index the next generated migration
    # deletes, and the only symptom would have been the portfolio and
    # customer-item screens quietly getting slower.
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_salestxn_source"),
        Index("ix_sales_txns_org_customer_product",
              "organization_id", "customer_id", "product_id"),
        Index("ix_sales_txns_org_product_date",
              "organization_id", "product_id", "date"),
    )

    sales_txn_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(160), index=True)  # invoice_id:line_id
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[Any] = mapped_column(Numeric(18, 4))
    # NET selling price per unit — after the line discount, which is what the
    # customer actually paid and the only figure a margin may be computed from.
    unit_price: Mapped[Any] = mapped_column(Numeric(18, 4))
    line_revenue: Mapped[Any] = mapped_column(Numeric(18, 4))   # pre-tax, post-discount
    # Audit trail for the price above, mirroring CostRecord. Nullable: rows
    # synced before the sales-discount fix have neither until the invoice is
    # re-fetched from Zoho (a full re-sync) — see docs/zoho-setup.md.
    rate: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    discount_percent: Mapped[Optional[Any]] = mapped_column(Numeric(9, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CostRecord(Base):
    """Bill-line grain (§4) — restricted (cost) data."""

    __tablename__ = "cost_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_costrecord_source"),
        # Declared for the same reason as the sales_txns pair above: it is what
        # makes the effective-cost lookup for a customer-item pair cheap.
        Index("ix_cost_records_org_product_date",
              "organization_id", "product_id", "date"),
        # The mirror of the pair above, for the other direction: "what did we
        # buy from this supplier, and when". Spend-by-supplier over a window
        # was a full scan without it.
        Index("ix_cost_records_org_vendor_date",
              "organization_id", "vendor_id", "date"),
    )

    cost_record_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(160), index=True)  # bill_id:line_id
    product_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Who this was bought from, copied down from the bill header.
    #:
    #: ``CostRecordIn`` has carried ``vendor_external_id`` since the supply pull
    #: landed, and the state reducers read it off the event payload — but the
    #: read model dropped it here, so the only way to ask "which items does this
    #: supplier actually supply" was to re-derive the bill id out of
    #: ``external_ref`` and join back to ``bills``. A dimension at line grain,
    #: not a measure: copying ``balance`` down would turn a sum into a
    #: de-duplication problem, copying the *vendor* down does not.
    #:
    #: Nullable, and left null rather than guessed. A bill from a supplier the
    #: vendor pull did not return is still a real cost — dropping the line to
    #: say who sold it would understate what an item cost.
    vendor_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                     ForeignKey("vendors.vendor_id"),
                                                     index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[Any] = mapped_column(Numeric(18, 4))
    # Effective, post-discount unit cost — every margin/pricing consumer reads this.
    unit_cost: Mapped[Any] = mapped_column(Numeric(18, 4))
    # Audit trail for the calculation above. Nullable: rows synced before the
    # discount-aware fix have neither, until the bill is re-fetched from Zoho —
    # see docs/zoho-setup.md for the backfill (discount was never stored locally,
    # so a re-sync is the only way to recover it for historical bills).
    rate: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    discount_percent: Mapped[Optional[Any]] = mapped_column(Numeric(9, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QuoteDraft(Base):
    """A quote the desk is working on — the Quote Builder's own row.

    The builder's quotes used to live in one process-wide dict (``store.py``)
    with a copy of the last one in the browser's ``localStorage``. That gave
    each person exactly one draft, visible to nobody else, gone from the server
    on the next restart, and numbered from a truncated clock. This table is the
    workspace instead: every draft in the organization, in one place, for
    everyone in it. The table was designed as "transient input for
    QUOTE_CONTEXT" and never written; ``quote_workspace`` is its writer now.

    **Canonical, not derived.** A draft exists in no ERP until it is sent, so
    a re-sync rebuilds nothing here — which is why it is not ``state/``.

    ``lines`` is the whole line list as ``store.Line.to_state`` writes it,
    cost included: the row is read back into the same dataclass and the
    role gate is applied on the way *out* (``Line.to_dict(mgmt)``), never on
    the way in. It is the server's copy, and the server may hold cost.

    ``sequence`` is what the number is made from: per organization, from 1,
    never reused, and unique by constraint so two desks starting a quote in
    the same instant cannot both be QB-0042.
    """

    __tablename__ = "quote_drafts"
    __table_args__ = (
        UniqueConstraint("organization_id", "sequence",
                         name="uq_quote_drafts_org_sequence"),
    )

    quote_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Nullable on purpose: a quote starts with no customer and gains one when
    #: the desk chooses. Empty is the honest default — the builder used to
    #: open every quote against one literal name.
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: The name to print, and what a typed (unpicked) customer resolves by.
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    #: Who started the draft. It is a shared workspace, so this attributes
    #: rather than scopes: a colleague may open, price and send it.
    salesperson_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: Who last changed it, so the list can say "R. Nair, 10:42".
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: The number the desk quotes by — ``QB-0042`` — and the integer it was
    #: minted from. See the class docstring for the uniqueness argument.
    number: Mapped[str] = mapped_column(String(32), default="")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    #: Which connected company's catalogue resolves every line on this quote.
    connection_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: The key any ERP document for this quote is written under — the whole of
    #: the send's idempotency (``store.Quote.reference``).
    reference: Mapped[str] = mapped_column(String(64), default="")
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)
    #: Removed from the workspace, and when. A row rather than a delete, for
    #: the number: it is minted as ``max(sequence) + 1`` over this table, so a
    #: deleted QB-0042 would hand its number to the next draft, and two quotes
    #: behind one number in somebody's notes is the defect the sequence exists
    #: to end. An archived draft is invisible to every read and holds its
    #: number for good.
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Signal (immutable deterministic fact, §6) ────────────────────────────────
class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_type: Mapped[str] = mapped_column(String(48), index=True)
    subject_entity_type: Mapped[str] = mapped_column(String(32))
    #: One id, or a **composite** one. A Customer × Item signal's subject is a
    #: pair — ``commercial.subject.encode`` joins two 36-char UUIDs with "::",
    #: which is 74 characters and does not fit in 64. SQLite enforces no
    #: declared length so it stored them happily; Postgres rejected the whole
    #: batch, and the failed flush poisoned the session that was writing the
    #: sync's own record. Sized like `business_states.key`, which was widened
    #: for exactly this reason one migration earlier — see `v2state_key_width`.
    subject_entity_id: Mapped[str] = mapped_column(String(160), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    detector_version: Mapped[str] = mapped_column(String(32))
    threshold_config_version: Mapped[str] = mapped_column(
        # ``"either"``: a Signal Engine detector stamps ``th_…`` while a
        # Customer × Item detector stamps ``ci_…``, and both land in this
        # one column. The registry reads the kind off the prefix.
        String(32), info={"policy_stamp": "either"})
    window: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity_base: Mapped[int] = mapped_column(Integer, default=0)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sufficiency: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # No updated_at and no update path: signals are write-once.


# ── Decision (primary object, §5) ────────────────────────────────────────────
class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("decision_key", name="uq_decision_key"),)

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_type: Mapped[str] = mapped_column(String(48), index=True)
    decision_key: Mapped[str] = mapped_column(String(128), index=True)  # idempotency (§17)
    subject_entity_type: Mapped[str] = mapped_column(String(32))
    #: Composite for a Customer × Item subject, exactly as on `Signal` — a
    #: decision carries its signal's subject through unchanged.
    subject_entity_id: Mapped[str] = mapped_column(String(160), index=True)
    assigned_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    assigned_role: Mapped[str] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # ── which producer made this, and what it was made from ─────────────────
    #
    # ``SIGNAL`` is the original path: a detector over sales and cost lines,
    # interpreted by the AI layer. ``STATE`` is the deterministic one: a
    # detector over folded Business State, which never touches ``ai/``.
    #
    # One table on purpose. A second "decision opportunity" table would mean
    # two lifecycles to keep in step, two queues, and two places a role-scoping
    # bug can hide — everything below this line already works for both.
    origin: Mapped[str] = mapped_column(String(16), default="SIGNAL", index=True)
    #: What the situation is worth, quantified. ``{financial, basis, monthly,
    #: operational}`` — money as strings, the same contract state values use.
    #: Empty for a signal-derived decision, which measures shape rather than
    #: money.
    impact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: The ``BusinessState`` keys this was computed from, and the day they were
    #: folded for. This is the join that closes the traceability chain:
    #: decision → state → transition → event → ERP record. Without it the chain
    #: exists in two halves that cannot be walked end to end.
    state_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    state_as_of: Mapped[Optional[date]] = mapped_column(Date)
    #: Why this exists, and what can be done about it. Both deterministic: the
    #: reason is assembled from the numbers that triggered the detector, and the
    #: actions are the ones this situation permits — the platform presents them
    #: and never chooses one.
    rationale: Mapped[Optional[str]] = mapped_column(String(2048))
    actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # AI sub-object (§5). Null/PENDING until the AI phase; never populated here.
    ai: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority_band: Mapped[str] = mapped_column(String(16), default="LOW")
    priority_score: Mapped[int] = mapped_column(Integer, default=0)
    priority_deterministic_base: Mapped[int] = mapped_column(Integer, default=0)
    priority_ai_adjustment: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    human_action: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    override_reason: Mapped[Optional[str]] = mapped_column(String(1024))
    outcome_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


# ── AI call telemetry (WS3) ──────────────────────────────────────────────────
class AiCallLog(Base):
    """One row per interpretation decision point — including cache hits and
    up-front suppressions, which never reach the provider.

    Operational/audit data only: it records how a call went (status, reason,
    latency, tokens, estimated cost), never prompt or response content.
    """

    __tablename__ = "ai_call_logs"

    ai_call_log_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_type: Mapped[str] = mapped_column(String(48), index=True)
    #: The subject the call was about, composite or not — same values as
    #: `Decision.subject_entity_id`, so the same width.
    subject_entity_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    recipient_role: Mapped[Optional[str]] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    context_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    ai_status: Mapped[str] = mapped_column(String(16), index=True)
    provider_called: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Optional[Any]] = mapped_column(Numeric(18, 8))
    failure_reason: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    corrections: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, index=True)


# ── Outcome (§8) ─────────────────────────────────────────────────────────────
class SyncRun(Base):
    """One ingestion run — the job record, from the moment it is queued.

    Persisted so the UI can answer "is Zoho connected, and when did data last
    arrive?" without re-hitting the API. A failed run is recorded too: silence
    about a failure is exactly what made the connection unreadable before.

    A run that dies part-way is ``PARTIAL``, not ``FAILED``: rows that did land
    are kept (they are what makes the next attempt cheap), and reporting zero
    for them would misdescribe the database.

    This is also the whole of the job model. A pull takes minutes, so it runs in
    the background and the row is what the UI polls — which means the row has to
    exist *before* the work starts, not only after it ends:

        QUEUED -> RUNNING -> OK | PARTIAL | FAILED

    ``heartbeat_at`` is what separates "running" from "died holding the lock".
    A process killed mid-pull cannot write its own failure, so without a
    heartbeat its row stays RUNNING forever and every later sync is refused as
    a duplicate. Staleness is judged from it rather than from ``started_at``,
    because a legitimate four-hour pull and a job that died after ten seconds
    look identical by start time.
    """

    __tablename__ = "sync_runs"
    __table_args__ = (
        # **The guard against two workers starting the same pull.** The check in
        # ``jobs.start_sync`` reads the active runs and then inserts, under a
        # ``threading.Lock`` — which is process-local, and the deployment runs
        # two uvicorn workers. Both reads returned "nothing running" before
        # either insert committed, so both inserted and both pulled: every
        # invoice fetched twice, two jobs racing on the same rows, and a
        # progress display that could not say which job its counter belonged
        # to. Only the database can exclude across processes, so the index is
        # the guard and the lock is a fast path in front of it.
        #
        # Two indexes rather than one because ``connection_id`` is nullable and
        # NULL is not equal to NULL: a single unique index over
        # (organization_id, connection_id) would let two umbrella runs — the
        # all-companies pull, which carries no connection — both insert. The
        # first index holds the per-connection case, the second the umbrella
        # one, and neither constrains the other, which is what keeps two
        # connected companies pulling at once (they are two independent APIs
        # and must not serialise).
        #
        # Partial, over the active rows only: a finished run must not stop the
        # next one, and the whole history of a connection's pulls stays in this
        # table. Both backends support partial indexes (SQLite since 3.8,
        # Postgres always), so this needs no dialect branch beyond naming the
        # predicate twice — the ``uq_value_event_key_live`` idiom.
        #
        # What the index cannot express, and what therefore stays in
        # ``start_sync``: the cross-exclusion between an umbrella run and a
        # per-connection run of the same organization. A unique index has no
        # way to say "no row in *that* group either".
        Index("uq_sync_run_active_connection",
              "organization_id", "connection_id", unique=True,
              sqlite_where=text(
                  "status IN ('QUEUED', 'RUNNING') AND connection_id IS NOT NULL"),
              postgresql_where=text(
                  "status IN ('QUEUED', 'RUNNING') AND connection_id IS NOT NULL")),
        Index("uq_sync_run_active_umbrella",
              "organization_id", unique=True,
              sqlite_where=text(
                  "status IN ('QUEUED', 'RUNNING') AND connection_id IS NULL"),
              postgresql_where=text(
                  "status IN ('QUEUED', 'RUNNING') AND connection_id IS NULL")),
    )

    sync_run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which company this run pulled. Null means every enabled connection, or a
    # run from before an organization could have more than one. Without it,
    # "last synced 2 hours ago" says nothing about *which* of three companies
    # it covered — the same ambiguity per-connection health was added to fix.
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16))           # "api" | "fixture"
    # QUEUED | RUNNING | OK | PARTIAL | FAILED
    status: Mapped[str] = mapped_column(String(16), index=True)
    # What the run is doing right now, in the words the screen shows —
    # "Reading invoices", not "phase 4". Null once the run is over.
    phase: Mapped[Optional[str]] = mapped_column(String(64))
    # Touched as the work proceeds. A RUNNING row whose heartbeat has gone cold
    # is a dead job, not a slow one, and must not block the next sync.
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Things the finished run wants to tell the person who started it — what
    # sample data it cleared out, what the metric rebuild found. These used to
    # ride back on the POST response; once the work happens after the response,
    # the row is the only place they can live.
    notes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # A long pull is read in calendar slices, so progress has a denominator that
    # is actually known: the months between the start date and today. This is
    # coverage of the requested window, not a prediction of remaining time —
    # months differ wildly in volume, and the screen says so.
    windows_total: Mapped[int] = mapped_column(Integer, default=0)
    windows_done: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    customers: Mapped[int] = mapped_column(Integer, default=0)
    products: Mapped[int] = mapped_column(Integer, default=0)
    sales_txns: Mapped[int] = mapped_column(Integer, default=0)
    cost_records: Mapped[int] = mapped_column(Integer, default=0)
    # The supply stage. ``SyncReport`` has counted these since the stage was
    # added, but there was nowhere to put them, so a run that read four hundred
    # suppliers reported nothing about them and the screen could only conclude
    # that suppliers were not being read at all. A counter that exists only in
    # memory is not a counter.
    vendors: Mapped[int] = mapped_column(Integer, default=0)
    stock_snapshots: Mapped[int] = mapped_column(Integer, default=0)
    payments: Mapped[int] = mapped_column(Integer, default=0)
    purchase_orders: Mapped[int] = mapped_column(Integer, default=0)
    # Commitments — what was promised in each direction but has not reached the
    # ledger. Counted separately from ``purchase_orders`` because a run can read
    # supplier orders perfectly while the sales-order scope is ungranted, and a
    # single "orders" figure would hide exactly that.
    sales_orders: Mapped[int] = mapped_column(Integer, default=0)
    vendor_payments: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_sample: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Skips folded by what is actually missing — one row per thing to fix,
    # with how many lines it blocks and where to find it. Stored alongside the
    # raw sample rather than replacing it: the sample is the evidence, this is
    # the worklist, and a truncated sample of four hundred identical rows was
    # neither.
    unresolved: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    signals_emitted: Mapped[int] = mapped_column(Integer, default=0)
    decisions_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(1024))
    triggered_by: Mapped[Optional[str]] = mapped_column(String(64))
    # The window this run asked for, and what the pull cost / saved.
    since: Mapped[Optional[date]] = mapped_column(Date)
    documents_fetched: Mapped[int] = mapped_column(Integer, default=0)
    documents_resumed: Mapped[int] = mapped_column(Integer, default=0)
    assignments: Mapped[int] = mapped_column(Integer, default=0)


class SyncSkip(Base):
    """One row a pull could not fully resolve, kept in full.

    ``SyncRun.skipped_sample`` holds the first twenty, and twenty is a preview
    rather than a record: a run that reported 1,304 skips persisted 20 of them
    and lost the other 1,284 when the job's process ended. The screen said "first
    20 of 1304" honestly enough, but there was no way to answer the only
    question that matters — *which* 1,284, and what do they have in common — so
    the gap could be seen and never worked. This table is what makes the list
    exportable and therefore fixable.

    Append-only per run, and **derived**: Zoho is the system of record, so a
    re-sync of the same window writes a new run with its own rows and the old
    ones stay as the account of what that run saw. Nothing computes off these —
    they are the evidence behind ``SyncRun.unresolved``, which is the worklist.

    Every context field is a real column rather than a JSON blob, because the
    entire point is a spreadsheet: a CSV built by walking a JSON dict has a
    column set that depends on which rows happened to carry which keys.

    ``line_value`` and ``qty`` are on a *purchase* line for a ``cost_record``
    skip, which makes them cost. Everything that serves this table is
    manager-or-owner only for that reason — see ``routers/data_status.py``.
    """

    __tablename__ = "sync_skipped_rows"
    __table_args__ = (
        # The one query this table has: every row of one run, in the order the
        # pull met them.
        Index("ix_sync_skips_run_seq", "sync_run_id", "seq"),
    )

    skip_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    sync_run_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Which connected company the skip happened in. A three-entity business
    #: reading three books needs this to answer "is this one company's master or
    #: all of them" — the merged report cannot say, so rows are written per
    #: service, before the merge.
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: Position within the run, so an export sorts back into the order the pull
    #: met these rather than into whatever order the rows come back in.
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(String(512), default="")
    # ── the context, flattened: what a person needs to find and fix the row ──
    missing_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(128))
    document: Mapped[Optional[str]] = mapped_column(String(128))
    document_date: Mapped[Optional[str]] = mapped_column(String(32))
    party: Mapped[Optional[str]] = mapped_column(String(255))
    qty: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    line_value: Mapped[Optional[Any]] = mapped_column(Numeric(18, 2))
    fix: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SyncRunLog(Base):
    """One line of what a sync actually did, kept where a person can read it.

    A pull runs for an hour in a background thread and the process log was the
    only account of it. So a failed run told whoever started it to "see the
    server log" — a person with a browser, no shell, and on a hosted platform
    quite possibly no log at all. This table is the answer to that sentence:
    the lines the run emitted, stored against the run, served to the screen
    that reports the failure.

    Append-only and **derived**, exactly like ``SyncSkip``: a re-run writes a
    new run with its own lines, and nothing computes off these. They are an
    account for a reader.

    ``seq`` orders them, because two lines written inside the same millisecond
    are not rare in a tight loop and a log that shuffles is not a log. The
    timestamp is the record's own, not the moment it was flushed — the flush
    happens at a phase boundary and would bunch a whole phase onto one instant.
    """

    __tablename__ = "sync_run_logs"
    __table_args__ = (
        # The only query: one run's lines, in order, optionally from a cursor
        # so a screen watching a live pull asks for what it has not seen.
        Index("ix_sync_run_logs_run_seq", "sync_run_id", "seq"),
    )

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    sync_run_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    #: When the line was *emitted*.
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    level: Mapped[str] = mapped_column(String(16), index=True)
    #: The logger that wrote it — "pie_portal.sync_jobs", "app.ingestion...".
    #: Kept because "who said this" is half of reading an interleaved log.
    logger: Mapped[str] = mapped_column(String(128), default="")
    #: The line itself. Text, not a bounded string: a traceback is one line by
    #: this table's reckoning and truncating it loses the frame that matters.
    message: Mapped[str] = mapped_column(Text, default="")


class IngestedDocument(Base):
    """One Zoho document already pulled — the resume cursor.

    Zoho's list endpoints omit line items, so every invoice and bill costs its
    own detail call. Recording what has been fetched (and the modification stamp
    it was fetched at) means an interrupted pull resumes for the price of the
    list calls alone, and a document edited in Zoho is re-fetched because its
    stamp moved.
    """

    __tablename__ = "ingested_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "connection_id", "doc_type", "doc_id",
                         name="uq_ingested_org_conn_type_doc"),
    )

    ingested_document_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Which connected company this document was read from.
    #:
    #: A Zoho document id is unique inside the Zoho organization that issued it
    #: and nowhere else, and this business runs three of them. Without this the
    #: cursor is shared: one company's id can suppress another's fetch, and a
    #: full sync of one company clears the cursor for all three.
    #:
    #: Nullable because rows written before this existed have no connection to
    #: name. They match a ``None`` connection, which is what an unscoped
    #: single-connection deployment still passes.
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)   # invoice | bill
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Zoho's ``last_modified_time``, rewritten onto the UTC line
    #: (``clock.utc_stamp``, ``YYYY-MM-DDTHH:MM:SSZ``) so the resume cursor's
    #: string ``max()`` is a time max across offset formats — kept verbatim
    #: only when the stamp cannot be placed there, and then excluded from the
    #: max by shape. ``ReadModelRepository.mark_ingested`` is the one writer.
    modified_at: Mapped[Optional[str]] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CustomerItemMetric(Base):
    """Derived Customer × Item commercial metrics — a recomputable projection.

    Holds no source facts of its own: every number here is computed from the
    ``SalesTxn`` and ``CostRecord`` rows for one (customer, product) pair, and
    the whole table can be dropped and rebuilt from them at any time
    (``python -m app.commercial.backfill``). It exists so the customer screen
    does not have to scan every invoice line in the organization on each page
    load — the one thing that would not survive thousands of customers ×
    thousands of items × years of history.

    ``thresholds_version`` and ``computed_at`` make any row reproducible: it
    says which threshold set and which moment produced these numbers.

    All of it is RESTRICTED (cost/margin) and never reaches a salesperson.
    """

    __tablename__ = "customer_item_metrics"
    __table_args__ = (
        UniqueConstraint("organization_id", "customer_id", "product_id",
                         name="uq_cim_org_customer_product"),
    )

    customer_item_metric_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                         default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64), index=True)

    # ── identity / activity ──────────────────────────────────────────────────
    first_transaction_date: Mapped[Optional[date]] = mapped_column(Date)
    last_transaction_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    history_months: Mapped[Optional[float]] = mapped_column(Float)

    # ── commercial position (recent window) ──────────────────────────────────
    revenue_recent: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    revenue_12m: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4), index=True)
    gross_profit_recent: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    gross_profit_12m: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    current_sell_price: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    current_effective_cost: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))

    # ── margin over periods (gross profit ÷ revenue, never a mean of percents)
    current_margin: Mapped[Optional[float]] = mapped_column(Float)
    previous_margin: Mapped[Optional[float]] = mapped_column(Float)
    margin_3m: Mapped[Optional[float]] = mapped_column(Float)
    margin_6m: Mapped[Optional[float]] = mapped_column(Float)
    margin_12m: Mapped[Optional[float]] = mapped_column(Float)
    historical_margin: Mapped[Optional[float]] = mapped_column(Float)

    # ── movement ─────────────────────────────────────────────────────────────
    margin_change_pp: Mapped[Optional[float]] = mapped_column(Float)   # percentage POINTS
    price_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    cost_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    erosion_kind: Mapped[Optional[str]] = mapped_column(String(24))    # COST_DRIVEN | …

    # ── same-item peer benchmark ─────────────────────────────────────────────
    same_item_median_price: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    same_item_median_margin: Mapped[Optional[float]] = mapped_column(Float)
    price_deviation_pct: Mapped[Optional[float]] = mapped_column(Float)
    margin_deviation_pp: Mapped[Optional[float]] = mapped_column(Float)
    peer_count: Mapped[int] = mapped_column(Integer, default=0)

    # ── volume ───────────────────────────────────────────────────────────────
    qty_recent: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    qty_previous: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    volume_change_pct: Mapped[Optional[float]] = mapped_column(Float)

    # ── economic impact (estimates, never "lost profit") ─────────────────────
    historical_margin_gap: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    peer_margin_gap: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    annualized_historical_margin_gap: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))

    # ── deterministic signal flags (what the detectors act on) ───────────────
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)

    # ── data quality ─────────────────────────────────────────────────────────
    data_sufficiency: Mapped[str] = mapped_column(String(16), default="INSUFFICIENT",
                                                  index=True)
    sufficiency_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    cost_covered_txns: Mapped[int] = mapped_column(Integer, default=0)
    cost_missing_txns: Mapped[int] = mapped_column(Integer, default=0)

    # ── provenance ───────────────────────────────────────────────────────────
    thresholds_version: Mapped[str] = mapped_column(
        String(32), default="", info={"policy_stamp": "commercial"})
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApprovalRequest(Base):
    """Something a person could not authorize on their own, and what came of it.

    This is the piece the platform was missing. A quote line below the margin
    floor was *computed*, *flagged* and *recorded* — and then went out anyway,
    because nothing anywhere refused it. A control that only annotates is not a
    control.

    The request carries a snapshot of what was being asked for (``subject``),
    not a live reference to it. If the salesperson re-prices the line while a
    manager is looking at the request, the manager must still see the number
    they were asked about; a request that silently re-points at whatever the
    price is *now* can be used to launder an approval.

    Immutable except for the decision fields. The thread of notes is append-only
    JSON for the same reason a quote decision is append-only: an approval
    argument that can be edited afterwards settles nothing.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_org_status", "organization_id", "status"),
        Index("ix_approval_org_subject", "organization_id", "kind", "subject_id"),
    )

    approval_request_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                     default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)

    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    # The minimum role that may decide this one (MANAGER | OWNER).
    required_authority: Mapped[str] = mapped_column(String(16), default="MANAGER")

    # What it is about. ``subject_id`` is the quote id or decision id; the
    # frozen detail lives in ``subject``.
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_line_id: Mapped[Optional[str]] = mapped_column(String(64))
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    title: Mapped[str] = mapped_column(String(255), default="")
    # Salesperson-safe summary. The approver additionally gets ``subject``,
    # which may carry cost and margin; this field never does.
    summary: Mapped[str] = mapped_column(String(1024), default="")
    reason_code: Mapped[Optional[str]] = mapped_column(String(48))
    reason: Mapped[Optional[str]] = mapped_column(String(2048))

    requested_by_user_id: Mapped[str] = mapped_column(String(64), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   default=_now, index=True)
    decided_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[Optional[str]] = mapped_column(String(2048))

    # Append-only conversation: [{at, user_id, name, action, note}]
    thread: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    thresholds_version: Mapped[str] = mapped_column(
        String(32), default="", info={"policy_stamp": "commercial"})


class OrgPolicy(Base):
    """One organization's approval policy — who must sign off on what.

    Separate from ``CommercialThresholds`` on purpose. Thresholds answer "is
    this price thin?" and are a property of the analysis; this answers "may it
    go out anyway, and whose call is that?" and is a property of the business.
    Conflating them means a company that wants a stricter sign-off has to
    distort its own margin analysis to get it.
    """

    __tablename__ = "org_policies"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # The master switch. Off, the platform advises and records but refuses
    # nothing — which is exactly where this product started.
    require_approval_for_quotes: Mapped[bool] = mapped_column(Boolean, default=True)
    # Below the *review* floor as well, not just the hard minimum. Off by
    # default: flagging every thin line for sign-off trains people to rubber
    # stamp, which is worse than not asking.
    require_approval_below_review_floor: Mapped[bool] = mapped_column(Boolean,
                                                                     default=False)
    # Selling under cost is the owner's call, not a manager's.
    below_cost_requires_owner: Mapped[bool] = mapped_column(Boolean, default=True)
    # A manager cannot approve their own request. Owners can, because in a small
    # business the owner is often the only approver and a rule they cannot
    # satisfy is a rule they will switch off entirely.
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    # Escalating a decision raises an approval request rather than closing it.
    escalation_creates_approval: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class QuoteDecision(Base):
    """An immutable snapshot of one priced quote line, at the moment it was priced.

    Append-only. Re-pricing a line writes a **new** row; nothing here is ever
    updated. That is the point: a quote decision is evidence about a judgement
    made against particular numbers on a particular day, and a row that can be
    edited afterwards proves nothing. If today's cost has moved, the old row
    must still say what it said — otherwise a margin review six months from now
    silently re-judges the salesperson against facts they never saw.

    The snapshot therefore carries the *values*, not references to them: the
    cost basis used, the references compared against, the exceptions that fired,
    and the threshold and engine versions that produced them.

    All economics here are RESTRICTED and never reach a salesperson.
    """

    __tablename__ = "quote_decisions"
    __table_args__ = (
        Index("ix_quote_decisions_org_quote", "organization_id", "quote_id"),
        Index("ix_quote_decisions_org_customer_product",
              "organization_id", "customer_id", "product_id"),
    )

    quote_decision_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    quote_id: Mapped[str] = mapped_column(String(64), index=True)
    quote_line_id: Mapped[str] = mapped_column(String(64))

    # ── what was quoted ──────────────────────────────────────────────────────
    # Refs are kept alongside the resolved ids: an unresolved item is still a
    # decision that was made, and dropping it would make the audit trail lie by
    # omission about exactly the lines with the least information behind them.
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    customer_ref: Mapped[str] = mapped_column(String(255), default="")
    product_ref: Mapped[str] = mapped_column(String(255), default="")

    quantity: Mapped[Any] = mapped_column(Numeric(18, 4))
    quantity_band: Mapped[str] = mapped_column(String(24), default="")
    quoted_unit_price: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))

    # ── the economics at that moment ─────────────────────────────────────────
    unit_cost: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    line_revenue: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    cogs: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    gross_profit: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    margin: Mapped[Optional[float]] = mapped_column(Float)

    # ── what it was judged against ───────────────────────────────────────────
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    exceptions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    relationship_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    data_sufficiency: Mapped[str] = mapped_column(String(16), default="INSUFFICIENT")
    sufficiency_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)

    # ── the human part ───────────────────────────────────────────────────────
    # An override is a price that went out despite a rule firing. The reason is
    # the single most valuable field in this table: it is how a threshold that
    # is wrong for the business gets found.
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason_code: Mapped[Optional[str]] = mapped_column(String(48))
    override_reason: Mapped[Optional[str]] = mapped_column(String(1024))
    overridden_exception_codes: Mapped[list[str]] = mapped_column(JSON, default=list)

    # ── provenance ───────────────────────────────────────────────────────────
    thresholds_version: Mapped[str] = mapped_column(
        String(32), default="", info={"policy_stamp": "commercial"})
    engine_version: Mapped[str] = mapped_column(String(32), default="")
    # Which pie-parser catalogue resolved the product on this line. Distinct
    # from `engine_version` above, which names the quote-intelligence engine:
    # this is the parser's own ruleset checksum, derived from the price-file
    # bytes plus the pack, and it is what explains later why the same RFQ text
    # resolved to a different product than it does today. Empty when the line
    # was not resolved through the engine.
    catalog_version: Mapped[str] = mapped_column(String(64), default="")
    as_of: Mapped[date] = mapped_column(Date)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 index=True)


class QuoteOutcome(Base):
    """Whether a quote was sent, and whether it was won.

    Per quote, not per line — a customer accepts or declines a quote, not a
    line. Kept in its own table precisely so that ``QuoteDecision`` can stay
    append-only: the outcome is learned later and must be mutable, the priced
    facts were true at the time and must not be.

    **The one table here a human writes and no sync touches.** Everything a
    person supplies about how a quote ended — the loss reason, who took it, the
    note — lives on this row and nowhere else, because ``quote_documents`` is
    rewritten wholesale from the ERP payload on every pull and anything typed
    into it would survive exactly until the next one. ``ingestion/`` never
    opens this table.

    A row names one of two documents, and exactly one:

    * a **platform quote** — ``quote_id`` set, and ``quote_document_ref``
      filled in at the moment the quote is pushed to the ERP, which is when the
      platform learns which ERP document its own quote became;
    * an **ERP-raised quote** the platform never priced — ``quote_id`` NULL,
      ``quote_document_ref`` set.

    ``quote_id`` is therefore nullable, and NULL is the honest encoding of
    "there is no platform quote" rather than a sentinel that would have to be
    excluded by every reader. NULLs are distinct in a unique index on both
    SQLite and PostgreSQL, so the two constraints below coexist over the same
    table with no partial index and nothing backfilled.

    "At least one of the two is set" is enforced in
    ``commercial.quote_service.set_outcome``, which is provably the only writer,
    rather than by a CHECK constraint: this repo has no CHECK precedent and
    ``compare_metadata`` does not compare them, so the drift test could never
    police one.
    """

    __tablename__ = "quote_outcomes"
    __table_args__ = (
        UniqueConstraint("organization_id", "quote_id", name="uq_quote_outcome_org_quote"),
        UniqueConstraint("organization_id", "quote_document_ref",
                         name="uq_quote_outcome_org_document"),
    )

    quote_outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                  default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The platform quote this describes. NULL for a quote raised directly in
    #: the ERP, which the platform reads but never priced.
    quote_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: The ERP document this describes: ``QuoteDoc.external_ref``, a *value* the
    #: source system issued — never ``QuoteDoc.quote_document_id``, which is a
    #: surrogate minted at insert. That is what makes a rebuild safe: ``DELETE
    #: FROM quote_documents`` plus a full re-sync re-mints every surrogate and
    #: every pointer here still resolves. Written the same way, and for the same
    #: reason, as ``PaymentApplication.invoice_external_ref``.
    #:
    #: It can dangle — an estimate deleted in the ERP, or one that falls outside
    #: the sync window, leaves a pointer matching no row. That is surfaced as a
    #: counted data-quality figure and never auto-cleaned: deleting a human's
    #: recorded loss reason because a document went missing is the larger loss.
    quote_document_ref: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    customer_ref: Mapped[str] = mapped_column(String(255), default="")
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    note: Mapped[Optional[str]] = mapped_column(String(1024))
    # Why it was lost — a ``QuoteLossReason``, and specifically whether the
    # money went to somebody else or the requirement died. Those two look
    # identical in a bare LOST row and mean opposite things about what this
    # customer spends elsewhere, so anything reasoning about that has to be
    # able to separate them, and a free-text ``note`` cannot be aggregated.
    #
    # NULL only on rows written before this column existed. Backfilling them to
    # UNKNOWN would be indistinguishable from somebody having answered
    # "unknown", so they are left NULL and read as "not recorded" — which is
    # the true statement. New losses cannot be NULL: ``set_outcome`` refuses a
    # LOST transition without a reason rather than defaulting to a benign one.
    # 32 rather than the 24 the longest member needs: it is the width
    # `claude/quote-win-loss` chose for this same column, and matching it means
    # whichever branch lands second deletes a migration instead of altering a
    # type on a live table.
    loss_reason: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    #: Who won it, where that is known. Free text on purpose — a competitor is
    #: not an entity this platform holds, and a lookup table of them would be a
    #: second customer master maintained by nobody. Never required: a reason is
    #: the part that has to be answerable, and a rep who does not know the
    #: winner must still be able to record the loss.
    lost_to: Mapped[Optional[str]] = mapped_column(String(255))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class QuoteDocument(Base):
    """A quote as it was written into a source system — one row per send.

    The durable half of a send. Everything about the estimate a quote produced
    lived on an in-memory dataclass in a process-wide dict, so the number died
    with the process: after a restart the screen could not say a quote had been
    sent, the local duplicate check could not answer, and nothing anywhere
    linked a quote to the document it had created in anybody's ledger. That
    link is what outcome attribution needs and never had.

    Append-only, and history in the §1 sense — it records what was written,
    when, under which policy — so it is never updated. A re-send of amended
    content writes another row; the newest is the current one. That is also why
    there is no unique key on ``fingerprint``: a crash between the source write
    and this insert must be recoverable by writing the row late, not by turning
    a recorded send into an integrity error.

    ``external_system`` is the connector key rather than a fixed word, because
    the whole point of the write seam is that this row will not always say
    "zoho" — and a column that assumed it would be the thing to migrate later.
    """

    __tablename__ = "quote_documents"

    quote_document_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    quote_id: Mapped[str] = mapped_column(String(64), index=True)

    #: The connector this was written into — ``connections.ZOHO_CONNECTOR`` and,
    #: in time, whatever else declares ``sales_quotes`` in its writes.
    external_system: Mapped[str] = mapped_column(String(32), default="")
    #: That system's own id for the document, where it returns one.
    external_document_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: The number a person sees and can search for in that system.
    external_document_number: Mapped[str] = mapped_column(String(64), default="")
    #: The caller-stable key the write carried, which is what lets the source
    #: recognise a repeat whose reply was lost. Kept because settling that
    #: question by hand means looking this up.
    reference: Mapped[str] = mapped_column(String(128), default="")
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    #: The priced content this document was written from. The local duplicate
    #: check compares it: equal means the quote has not changed since it was
    #: sent, and pressing again should return this document rather than make
    #: another.
    fingerprint: Mapped[str] = mapped_column(String(128), default="", index=True)
    #: True where the source recognised the reference and returned a document it
    #: already held, rather than creating one. Recorded because "sent" and "was
    #: already there" are different facts and the screen says so.
    already_existed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The commercial policy in force when this went out. A signed, append-only
    #: row keeps the version that judged it — the same rule approvals and
    #: snapshots follow, and the reason a past send stays explainable after the
    #: margin policy is edited.
    #:
    #: Marked, so ``threshold_registry`` records its pre-image on the flush that
    #: writes it. Without the marker the stamp is stored and the values behind
    #: it are not, which makes the row an audit trail of a hash — the exact
    #: state the registry was built to end.
    thresholds_version: Mapped[str] = mapped_column(
        String(64), default="", info={"policy_stamp": "commercial"})
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Outcome(Base):
    __tablename__ = "outcomes"

    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("decisions.decision_id"), index=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    human_action_summary: Mapped[Optional[str]] = mapped_column(String(1024))
    measurement_window: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    measured_metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    impact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    decision: Mapped["Decision"] = relationship()


class OutcomeSnapshot(Base):
    """What was true at the moment a recommendation was accepted — frozen.

    Written once by ``commercial.outcome_tracker.capture_on_accept`` when a
    signal-derived decision is accepted, and never updated: the baseline a
    realised delta is measured against must be the one that was in force when
    the human said yes, not whatever a later re-sync recomputed. Evaluation is
    *derived* — ``commercial.outcome_tracker.evaluate`` recomputes the realised
    position from ``SalesTxn``/``CostRecord`` rows on read — so nothing here
    ever needs correcting in place.

    Not a widening of ``Outcome`` above, deliberately. That table models a
    recorded *measurement* (``measured_metrics``, ``impact``, a mutable
    ``status``) and has never been written; this one models the *baseline* the
    measurement will one day be made against. One table holding both would hold
    two lifecycles — the same reason ``EvaluationBaseline`` is not columns on
    ``IntelligenceTrial``.

    ``thresholds_version`` is copied **verbatim** from the signal's own
    ``threshold_config_version`` — ``th_…`` for engine signals, ``ci_…`` for
    Customer × Item signals. The two stamps are different hashes over different
    policies; this column records whichever one actually judged the signal and
    must never be read as the other.

    ``horizon_days`` stores the literal number of days, not a pointer into the
    horizon config: the value in force at acceptance is what the evaluation
    window is built from, and a later config edit must not move a window that
    somebody's acceptance already anchored.

    ``baseline_metrics`` for the restricted categories embeds cost and margin
    figures. The router omits those fields server-side for a salesperson —
    absent from the response, not hidden in the browser (§1).
    """

    __tablename__ = "outcome_snapshots"
    __table_args__ = (
        # One snapshot per decision. Accept → reopen → accept again keeps the
        # first baseline: the horizon is anchored at the first acceptance, and
        # a second row would be a second claim about one recommendation.
        UniqueConstraint("decision_id", name="uq_outcome_snapshot_decision"),
        Index("ix_outcome_snapshots_org_category", "organization_id", "category"),
        Index("ix_outcome_snapshots_org_accepted", "organization_id", "accepted_at"),
    )

    outcome_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                     default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("decisions.decision_id"))
    #: The signal whose evidence the baseline was read from. Non-null by
    #: construction: v1 captures only signal-derived decisions (a STATE
    #: decision has no signal baseline to freeze, and quantifies impact through
    #: different machinery).
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The signal's type — CUSTOMER_DECLINE, MARGIN_DETERIORATION, … — which is
    #: what selects the evaluator and the default horizon.
    category: Mapped[str] = mapped_column(String(48), index=True)
    subject_entity_type: Mapped[str] = mapped_column(String(32))
    #: Copied verbatim from the signal, so it inherits the signal's width.
    subject_entity_id: Mapped[str] = mapped_column(String(160), index=True)
    #: The signal's ``metrics``, copied whole. This is the baseline later
    #: evaluation compares against, kept even if the signal row is ever erased.
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: The signal's ``window`` — which periods the baseline was measured over,
    #: so a realised figure can say whether its window is comparable.
    baseline_window: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thresholds_version: Mapped[str] = mapped_column(
        # ``"either"`` and not ``"commercial"``: this column is copied verbatim
        # from the signal, so it holds ``th_…`` as often as ``ci_…``. The
        # registry derives the kind from the prefix for exactly this reason —
        # a marker asserting the wrong kind would make every engine-signal
        # acceptance look like an unrecorded commercial policy.
        String(32), default="", info={"policy_stamp": "either"})
    horizon_days: Mapped[int] = mapped_column(Integer)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ── Value attribution ────────────────────────────────────────────────────────
#
# What the platform is worth to the business, measured rather than asserted.
# ``Outcome`` above cannot carry this: its ``decision_id`` is non-nullable, so
# it can only ever describe value that arrived through a decision card — and
# most of the money this platform touches moves on the Quote Desk, which writes
# ``QuoteDecision`` rows and no decision at all. Widening ``Outcome`` to make
# that FK optional would leave one table with two meanings and a nullable join
# that no query could safely assume either way.


class ValueEvent(Base):
    """One measured rupee outcome, tied to the evidence that establishes it.

    Append-only, like ``QuoteDecision`` and for the same reason: this is the
    ledger a renewal conversation is argued from, and a row that can be edited
    afterwards proves nothing. A superseded event is superseded by a *new* row,
    never by an update.

    ``event_key`` is unique per organization because double counting is the
    failure mode this table exists to survive. A rollup that runs twice, a
    detector re-run after a re-sync, a quote whose outcome is recorded again —
    each is an ordinary occurrence, and each would otherwise inflate the total
    the business is being asked to pay against. The key is derived from
    (event_type, evidence identity), so recording the same fact twice is a no-op
    at the *database* level rather than a code path that has to remember. This
    is the ``Decision.decision_key`` idiom (``uq_decision_key``) applied to
    money, where getting it wrong is not a duplicate card but a false claim.

    ``amount`` is nullable and that is not laxity. Some classes carry no money —
    a counted intervention with no defensible rupee value must be recordable as
    itself rather than as a zero, because zero is an *amount* and would sum. A
    line whose ``unit_cost`` is missing produces no event here at all; absence of
    evidence is UNKNOWN, never a benign zero (§1).

    ``basis`` holds the operands the amount was computed from — the floor price,
    the final price, the quantity — so the drill-down can re-derive the number
    instead of restating it. An event nobody can re-derive is an assertion, and
    ``evidence_refs`` is the other half of that: it is never empty, because an
    event that cannot name what it was computed from has nothing to defend.

    All economics here are RESTRICTED. Attributed value on a quote line *is*
    ``gross_profit`` arithmetic, so this surface never reaches a salesperson —
    absent from the response, not hidden in the browser.
    """

    __tablename__ = "value_events"
    __table_args__ = (
        # Unique over the *live* rows only. A plain unique constraint was wrong
        # once supersession existed: correcting a measurement writes a second row
        # with the same key, and the constraint would refuse the correction. The
        # partial index keeps the guarantee that matters — one live claim per
        # fact — while letting the superseded ones stay as history.
        #
        # Both backends support partial indexes (SQLite since 3.8, Postgres
        # always), so this needs no dialect branch beyond naming the predicate
        # twice.
        Index("uq_value_event_key_live", "organization_id", "event_key",
              unique=True,
              sqlite_where=text("superseded_at IS NULL"),
              postgresql_where=text("superseded_at IS NULL")),
        Index("ix_value_events_org_occurred", "organization_id", "occurred_at"),
        Index("ix_value_events_org_class", "organization_id", "value_class"),
        Index("ix_value_events_org_superseded", "organization_id", "superseded_at"),
    )

    value_event_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)

    # A ``ValueEventType``. What happened.
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    # A ``ValueClass``. How strong the evidence is — and therefore which total
    # this row is allowed to be added into. The classes never sum together.
    value_class: Mapped[str] = mapped_column(String(16))
    event_key: Mapped[str] = mapped_column(String(128))

    amount: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    # Stored per row rather than assumed: the books are INR today, and a total
    # that silently mixes currencies is worse than one that refuses to.
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    basis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # When the *business fact* happened, which is not when this row was written.
    # A rollup for last month must include an event detected today about a quote
    # won three weeks ago, so every window query reads this column.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    thresholds_version: Mapped[str] = mapped_column(
        String(32), default="", info={"policy_stamp": "commercial"})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Set when a later detection run measured this same fact differently — a
    #: line repriced again, so the earlier amount is now an understatement. The
    #: superseded row is kept and every rollup filters ``IS NULL``, which is what
    #: makes a re-run correct the ledger instead of accumulating into it.
    #:
    #: This is the one stamp written to an existing row, and it is why the table
    #: can still be called append-only: the *claim* is immutable, and this only
    #: records that a newer claim replaced it. Without it, a stable event key
    #: would freeze the first amount forever, and a changing key — which is what
    #: shipped first — let three runs over one line bank three overlapping
    #: amounts for one price movement.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EvaluationBaseline(Base):
    """What the numbers looked like before the trial, so "better" means something.

    Its own table rather than columns on ``IntelligenceTrial``, and the reason is
    lifecycle rather than tidiness. A trial row is an **entitlement fact**: it
    records that one set of books has had its free month, it must survive
    everything, and it is never rebuilt. A baseline is **derived state** — a
    measurement over rows a complete re-sync re-derives from Zoho, and therefore
    something a recompute is entitled to rewrite. Putting them in one table would
    put those two lifecycles in one row, where a routine baseline recompute could
    destroy the record that stops a free month being minted twice.

    ``evidence_gaps`` names what could not be measured. If there is not enough
    history behind ``window_start``, the baseline says so and the report says the
    comparison is not available — it does not quietly compare against a thin
    window and call the difference improvement.
    """

    __tablename__ = "evaluation_baselines"
    __table_args__ = (
        Index("ix_evaluation_baselines_org_captured", "organization_id", "captured_at"),
    )

    baseline_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    trial_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("intelligence_trials.trial_id"), index=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The period measured — the days *before* the trial began, not the trial.
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)

    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    thresholds_version: Mapped[str] = mapped_column(
        String(32), default="", info={"policy_stamp": "commercial"})


# ── Identity layer ───────────────────────────────────────────────────────────
#
# The platform reads from several ERPs at once — two Zoho companies today, a
# Tally company and an ERPNext instance tomorrow. The same real customer exists
# in all of them under different ids, and the same item under different codes.
#
# The rule that shapes every table below: **connector data is never merged.**
# Each connector stays the source of truth for its own records, which are stored
# exactly as they arrive. An identity is a thin, connector-agnostic node that
# says "these records are the same business entity" — nothing more. Merging
# would destroy the one thing an ERP integration must preserve, which is the
# ability to point at a figure and say which system it came from.
#
# Consequently an identity row holds no connector fields at all. Its display
# name is derived from the records linked to it, or set by a person; it is never
# copied from whichever connector happened to be read first, because that would
# quietly make one connector authoritative over the others.


class CustomerIdentity(Base):
    """One real-world customer, across every connector that knows them.

    Deliberately almost empty. Everything about the customer — name, GSTIN,
    address — belongs to the connector records; this exists only to be pointed
    at. A ``label`` is the exception: it is what a *person* chose to call this
    entity, which is not connector data.
    """

    __tablename__ = "customer_identities"

    identity_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    # Retired rather than deleted when its last record is unlinked, so the audit
    # trail keeps pointing at something real.
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class CustomerConnectorRecord(Base):
    """One customer as one connector holds it. Immutable source data.

    Keyed on (connector, connection, external id) rather than on the external id
    alone: "CUST-102" from ERPNext and contact 12345 from Zoho are different
    records that may well collide numerically, and two Zoho companies under one
    organization can each hold their own id space.
    """

    __tablename__ = "customer_connector_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_id",
                         name="uq_customer_record_source"),
        Index("ix_customer_record_gstin", "organization_id", "gstin"),
    )

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    identity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customer_identities.identity_id"), index=True)

    # Which system, and which instance of it. ``connection_id`` is null for a
    # connector that has only one instance; ``connector`` is never null.
    connector: Mapped[str] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)

    name: Mapped[str] = mapped_column(String(255), default="")
    # Normalised on write (upper, no spaces) so matching never depends on how a
    # given ERP formats it. The raw value stays in ``source_ref``.
    gstin: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    # The read-model row this record projects into, when there is one. The link
    # is here rather than on Customer so the read model stays a projection.
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)

    identity: Mapped["CustomerIdentity"] = relationship()


class ItemIdentity(Base):
    """One real-world item, across every connector that stocks it."""

    __tablename__ = "item_identities"

    identity_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class ItemConnectorRecord(Base):
    """One item as one connector holds it. Immutable source data."""

    __tablename__ = "item_connector_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_id",
                         name="uq_item_record_source"),
        Index("ix_item_record_sku", "organization_id", "sku"),
    )

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    identity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("item_identities.identity_id"), index=True)

    connector: Mapped[str] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)

    sku: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    product_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)

    identity: Mapped["ItemIdentity"] = relationship()


class IdentitySuggestion(Base):
    """A proposed link, awaiting a person.

    Automatic linking is off by default, so an import that finds an exact GSTIN
    or SKU match does not act on it — it records this and asks. The alternative
    is a system that silently decides two companies are one, which is
    unrecoverable by the time anyone notices: the evidence of the mistake is the
    thing the merge destroyed.
    """

    __tablename__ = "identity_suggestions"
    __table_args__ = (
        UniqueConstraint("record_id", "target_identity_id",
                         name="uq_suggestion_record_target"),
        Index("ix_suggestion_open", "organization_id", "entity_type", "status"),
    )

    suggestion_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))     # CUSTOMER | ITEM
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    target_identity_id: Mapped[str] = mapped_column(String(64), index=True)

    # Which rule proposed it, and on what value. Named so a reviewer can judge
    # the suggestion instead of trusting a score: "GSTIN 29ABCDE1234F1Z5" is
    # reviewable, "0.97" is not.
    strategy: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)

    decided_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IdentityEvent(Base):
    """Append-only history of every link, unlink and merge decision.

    Write-once, like ``Signal``. The value of an identity layer is that it can
    be argued with later, and an audit trail that can be edited cannot settle an
    argument.
    """

    __tablename__ = "identity_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(16), index=True)
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    record_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # CREATED | LINKED | UNLINKED | SUGGESTED | SUGGESTION_ACCEPTED |
    # SUGGESTION_REJECTED | RELABELLED | RETIRED
    action: Mapped[str] = mapped_column(String(32), index=True)
    # AUTO when a rule acted under an explicitly enabled setting; otherwise the
    # user id. Never blank — "who decided this" is the first question asked of a
    # link somebody disagrees with.
    actor: Mapped[str] = mapped_column(String(64), default="SYSTEM")
    detail: Mapped[str] = mapped_column(String(512), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class IdentityPolicy(Base):
    """Whether the platform may link without asking.

    Its own row rather than a field on ``OrgPolicy``, whose docstring makes the
    point itself: that table answers "may this price go out, and whose call is
    that?". Whether two ERP records describe one company is a data-stewardship
    question, not an approval one, and the two should not have to move together.
    """

    __tablename__ = "identity_policies"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Off by default. An exact GSTIN match is strong evidence, not proof: a
    # group can register several trading names against one GSTIN, and undoing a
    # wrong link after three months of analysis has been built on it is far more
    # expensive than confirming it once.
    auto_link_customers: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_link_items: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


# ── Trust controls (per-tenant keys, name vault, access, disclosure) ─────────
class TenantKey(Base):
    """One data key per organization, wrapped by the process master key.

    The row outlives its key material. After ``destroy`` the wrapped key is
    blank and ``destroyed_at`` is set — a tombstone, so "was this tenant erased,
    when, and who asked for it?" has an answer. Deleting the row instead would
    make an erasure indistinguishable from a tenant that never existed.
    """

    __tablename__ = "tenant_keys"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wrapped_dek: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    destroyed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    destroyed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    destroy_reason: Mapped[Optional[str]] = mapped_column(String(512))
    # Set when a key that could no longer be unwrapped was replaced with a
    # fresh one (``keys.reissue``). Recorded for the same reason the
    # destruction tombstone is: replacing a key makes everything written under
    # the old one permanently unreadable, and an operation with that
    # consequence must not be inferable only from a shell history.
    reissued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reissued_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    reissue_reason: Mapped[Optional[str]] = mapped_column(String(512))


class NameVaultEntry(Base):
    """A display name, encrypted under its own tenant's data key.

    Names are the only identifying data here; everything else is quantities and
    dates. Holding them apart is what lets the analytical core — and anything
    leaving for a model — work in pseudonyms.
    """

    __tablename__ = "name_vault"
    __table_args__ = (
        UniqueConstraint("organization_id", "entity_type", "entity_id",
                         name="uq_name_vault_entity"),
    )

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(16), index=True)  # CUSTOMER | PRODUCT
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    name_ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class AccessGrant(Base):
    """Break-glass: one staff member, one tenant, one stated reason, time-boxed."""

    __tablename__ = "access_grants"

    grant_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    staff_user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Shown to the customer verbatim. That it is customer-visible is what makes
    # it get written honestly.
    justification: Mapped[str] = mapped_column(String(1024))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AccessEvent(Base):
    """Every grant, revocation and individual reach — the customer-visible log.

    Per-use rather than per-grant: opening the door once and opening it fifty
    times are different facts, and only per-use records tell them apart.
    """

    __tablename__ = "access_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    access_grant_id: Mapped[str] = mapped_column(String(64), index=True)
    staff_user_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(16), index=True)  # GRANTED|ACCESSED|REVOKED
    detail: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, index=True)


class AuditEntry(Base):
    """One tamper-evident link in a tenant's audit chain.

    The question this table exists to answer is "who saw this customer's
    economics, when, under which policy, and what did the AI say?" — and audit
    coverage cannot be backfilled, so a day without a row here is permanently
    unauditable. Six append-only surfaces already existed (``business_events``,
    ``value_events``, ``access_events``, ``ai_call_logs``, the approval thread,
    ``identity_events``) and not one of them was both *principal-bearing* and
    *linked*: an append-only table whose rows can be individually rewritten is
    append-only by convention, and a convention is what an auditor is being
    asked to take on faith.

    **The chain.** Each entry carries the ``entry_hash`` of the one before it in
    ``prev_hash``, and its own ``entry_hash`` is an HMAC over a body that
    includes that link (``trust/audit.py`` builds it, ``trust/signing.py``
    signs it). Rewriting one row therefore invalidates it and every row after
    it, and re-signing them all needs the deployment secret as well as the
    database.

    **Why the ordering is a constraint and not a lock.** A chain needs a total
    order and a single writer, and ``app/leases.py`` refuses in writing to be
    that writer — it has no fence token, so a leader can hold an unexpired claim
    while frozen and resume after another worker has taken it. The order is
    therefore enforced where the write lands: ``uq_audit_entry_org_seq`` means
    two appenders cannot both hold position N, and the loser gets an
    ``IntegrityError``, re-reads the head and retries. The same idiom as
    ``uq_sync_run_active_connection`` — the database refuses, rather than a
    process-local lock being trusted.

    ``uq_audit_entry_org_prev`` states the property directly rather than as a
    consequence: two entries naming the same parent *is* a fork, whatever their
    sequence numbers say. The sequence constraint already makes that
    unreachable while ``seq`` is always ``head.seq + 1``; this one holds even if
    that arithmetic is ever wrong, which is exactly the case a constraint is
    for.

    **Scope is the organization.** One chain per tenant, so a tenant's history
    verifies on its own, exports on its own, and two tenants' writes never
    contend for a position. There is deliberately no cross-tenant chain: it
    would serialise every audited action in the deployment behind one row, and
    a global order is not a question anybody asks of this log.
    """

    __tablename__ = "audit_entries"
    __table_args__ = (
        # The whole ordering argument, in two indexes. Not partial: every row
        # here is live forever — an audit entry that could be superseded would
        # be an audit entry that could be edited.
        UniqueConstraint("organization_id", "seq", name="uq_audit_entry_org_seq"),
        UniqueConstraint("organization_id", "prev_hash",
                         name="uq_audit_entry_org_prev"),
        Index("ix_audit_entries_org_seq", "organization_id", "seq"),
        Index("ix_audit_entries_org_action", "organization_id", "action"),
        Index("ix_audit_entries_org_at", "organization_id", "at"),
    )

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Position in this organization's chain, 1-based and dense. Allocated by
    #: reading the head and adding one; the unique constraint above is what
    #: makes that safe under concurrency rather than merely usual.
    seq: Mapped[int] = mapped_column(Integer)
    #: The previous entry's ``entry_hash``. Sixty-four zeros for the genesis
    #: entry — a literal rather than NULL, because NULL is not equal to NULL and
    #: a unique index over it would let two genesis entries in.
    prev_hash: Mapped[str] = mapped_column(String(64))
    #: HMAC-SHA256 over this entry's covered body, which includes ``prev_hash``.
    entry_hash: Mapped[str] = mapped_column(String(64))

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: What happened, from ``trust/audit.Action``. A string rather than an enum
    #: column so an old row keeps naming what it named after the vocabulary
    #: grows — a chain whose stored value stops being readable is a chain that
    #: stops verifying.
    action: Mapped[str] = mapped_column(String(48))

    #: The account that acted, when there was one. NULL for a failed sign-in
    #: against an address with no account, and for work no person asked for.
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: Who acted, as a human reads it — an email, or ``SYSTEM``. Never blank,
    #: the ``IdentityEvent.actor`` rule: "who did this" is the first question
    #: asked of an entry somebody disagrees with.
    actor_label: Mapped[str] = mapped_column(String(255), default="SYSTEM")
    #: The role the actor held *at the time*, which is the one an audit wants —
    #: a later promotion must not rewrite what authority an act carried.
    actor_role: Mapped[str] = mapped_column(String(32), default="")

    #: What was acted on: a stable type name and its id (e.g. ``POLICY`` /
    #: the organization, ``APPROVAL_REQUEST`` / the request id).
    subject_type: Mapped[str] = mapped_column(String(32), default="")
    subject_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    #: The structured particulars, covered by the signature like everything
    #: else. Deliberately not a free-text sentence: a rendered string cannot be
    #: queried and drifts from what it describes.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    #: The policy stamp in force when this happened — ``ci_…`` from
    #: ``CommercialThresholds`` or ``th_…`` from ``SignalThresholds``. §1: these
    #: are different stamps and must not be read as one, so the value carries
    #: its own prefix and ``detail`` says which question it answers.
    #:
    #: ``"either"`` rather than a fixed kind, because this column genuinely
    #: carries both: ``kind_of`` reads the prefix off the value. A signed,
    #: append-only entry is precisely the row that must stay explainable after
    #: the policy behind it is edited.
    thresholds_version: Mapped[Optional[str]] = mapped_column(
        String(32), info={"policy_stamp": "either"})

    #: Which process wrote it (``leases.holder_id()``). Not part of authority —
    #: it is there so a break in the chain can be placed against a deployment.
    source: Mapped[str] = mapped_column(String(128), default="")


class AuditChainHead(Base):
    """Where a tenant's chain is *supposed* to end. The anchor against truncation.

    Without this the chain is only half tamper-evident, and the missing half is
    the half an attacker uses. Modifying a row breaks its signature and every
    link after it; inserting one breaks the sequence. But **deleting from the
    tail breaks nothing**: remove the newest N entries and what remains is a
    shorter chain that is internally perfect, verifies clean, and silently heals
    as soon as the next entry is appended onto the new end. Demonstrated on a
    real database during review — delete every row and the verdict was still
    "ok". A log you can chop the incriminating end off is not tamper-evident,
    and the incriminating end is exactly where the interesting rows are.

    So the expected end is recorded separately and updated in the same
    transaction as the entry. ``verify`` compares what it walked against this,
    and a chain shorter than its anchor is reported ``TRUNCATED``.

    **What this proves, stated narrowly, because the difference matters to
    whoever relies on it.** It raises truncation from a single ``DELETE`` to a
    consistent edit of two tables — worthwhile, and roughly the bar the rest of
    this schema holds. It is *not* an external witness: anyone who can write
    arbitrary SQL can still move the anchor to match a shortened chain, and no
    same-database scheme can prevent that, because the evidence and the party
    being constrained share a home. The stronger claim needs the head hash
    copied somewhere the database cannot reach — an export kept off-box, a
    counter-signature, a witness service. ``GET /trust/audit/verify`` returns
    the head hash so that can be done without further code, and until it is,
    the honest description of this log is "tamper-evident against anything short
    of database write access", not "tamper-proof".
    """

    __tablename__ = "audit_chain_heads"

    #: One row per tenant, and the tenant is the identity — there is no
    #: surrogate key, because a second row for an organization would itself be
    #: the ambiguity this table exists to remove.
    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The ``seq`` of the last entry appended. An entry count as well as a
    #: position, since the chain is dense from 1.
    seq: Mapped[int] = mapped_column(Integer)
    #: That entry's ``entry_hash``. Compared as well as the count: a chain
    #: truncated and then re-grown to the same length has the right seq and the
    #: wrong hash.
    entry_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)


class ModelPayload(Base):
    """Exactly what was sent to a model provider, encrypted under the tenant key.

    ``AiCallLog`` records how a call went and holds no content, which is right
    for operational telemetry and leaves "what did you say about my business?"
    unanswerable. This answers it. Encrypted because a table of every payload
    ever sent is precisely the table worth stealing.
    """

    __tablename__ = "model_payloads"

    payload_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    ai_call_log_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    decision_type: Mapped[str] = mapped_column(String(48), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    payload_ciphertext: Mapped[str] = mapped_column(Text)
    # Anything the published disclosure forbids that was found in this payload.
    # Should always be empty; a non-empty list is a defect report.
    disclosure_findings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, index=True)


class ErasureReceipt(Base):
    """Proof of what was destroyed — and what was not — signed, readable after.

    Stored unencrypted on purpose: a receipt sealed under the key whose
    destruction it certifies would be unreadable exactly when it is wanted.
    """

    __tablename__ = "erasure_receipts"

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: What the erasure claimed at the moment it ran: ``method``, ``destroyed``
    #: (the field classes encrypted under the DEK, which key loss unreads) and
    #: ``survives_plaintext`` (the columns key destruction cannot touch, each
    #: with its reason). Stamped by ``trust/erasure.erase`` and covered by the
    #: signature — kept on the row rather than re-read from code so a later
    #: edit to those lists neither rewrites an old receipt's story nor breaks
    #: its verification.
    attestation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(512), default="")
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    erased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                default=_now, index=True)
    signature: Mapped[str] = mapped_column(String(128), default="")


# ── supply, stock and cash: the three things the book knew and the platform ──
# did not
#
# These are *ingested facts*, in the same class as SalesTxn and CostRecord: raw
# rows from Zoho with a source_ref, no interpretation and no thresholds_version.
# The the book views views compute from them at request time, the way every other
# insight module computes from the sales snapshot. Nothing here decides
# anything; a stock number that has been rounded, banded or judged on its way
# in is a stock number nobody can reconcile against Zoho.


class Vendor(Base):
    """A supplier. Deliberately the same shape as ``Customer``.

    Zoho holds both in ``contacts`` distinguished by ``contact_type``, and the
    temptation is to reuse the customers table with a flag. That would put two
    entities with different lifecycles, different identity keys and different
    scope rules in one place — and every query in the platform would grow a
    ``WHERE kind = ...`` that somebody eventually forgets.
    """

    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id",
                         "external_id", name="uq_vendor_source"),
    )

    vendor_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # See ``Customer`` for why the source is part of the key, not decoration.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    #: Registration ids, the strongest identity keys available for a supplier.
    #: Absent on plenty of small vendors, which the matcher reads as "no
    #: evidence" rather than "no match".
    gstin: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    pan: Mapped[Optional[str]] = mapped_column(String(32))
    #: Agreed payment days, as Zoho holds them. Zero means "due on receipt",
    #: which is a real term and not a missing value.
    payment_terms_days: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class StockSnapshot(Base):
    """What Zoho said was on the shelf, on one day.

    Zoho reports stock as a *current* number with no history, so history only
    exists if something writes it down. One row per item per day, upserted, so a
    platform that has been running for a month can draw a month and one that
    started yesterday draws a point — and says so, rather than interpolating a
    line through a single observation.

    ``committed`` is derived by Zoho, not here: ``available_stock`` is what can
    still be sold and ``actual_available_stock`` nets off what is already
    promised, so a negative actual against a positive available is the honest
    signal that more has been committed than exists.
    """

    __tablename__ = "stock_snapshots"
    __table_args__ = (
        UniqueConstraint("organization_id", "product_id", "as_of",
                         name="uq_stock_org_product_day"),
        Index("ix_stock_org_asof", "organization_id", "as_of"),
    )

    stock_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("products.product_id"),
                                            index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    on_hand: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    available: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Nets off stock already committed to open sales orders. May be negative.
    actual_available: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Zoho's reorder point. Blank on most items in practice, and a blank must
    #: never be read as zero — "no reorder point set" is a different statement
    #: from "reorder at zero", and only one of them is a policy.
    reorder_level: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Last purchase price, as Zoho holds it. Cost — manager scope only.
    purchase_rate: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: False for services and non-inventory items, which have no stock to speak
    #: of and must not be counted as "zero on hand".
    tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaymentReceipt(Base):
    """Money in, at the payment grain."""

    __tablename__ = "payment_receipts"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_payment_source"),
        Index("ix_payment_org_date", "organization_id", "date"),
    )

    payment_receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                    default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)  # Zoho payment_id
    customer_id: Mapped[str] = mapped_column(String(64),
                                             ForeignKey("customers.customer_id"),
                                             index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Any] = mapped_column(Numeric(18, 4))
    mode: Mapped[Optional[str]] = mapped_column(String(64))
    #: An advance is money against no invoice yet. It must not enter a
    #: days-to-pay average, because there is no invoice date to subtract.
    is_advance: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Received but not yet applied to any invoice.
    unapplied_amount: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class PaymentApplication(Base):
    """One payment against one invoice — the row days-to-pay is computed from.

    The invoice's own date and due date are stored here rather than joined to
    ``sales_txns``, because an invoice can predate the sync window: a payment
    landing today may settle an invoice from before the platform's history
    starts, and a join would silently drop exactly the slow payments that
    matter most.
    """

    __tablename__ = "payment_applications"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_payment_application_source"),
        Index("ix_payment_app_org_invoice", "organization_id", "invoice_external_ref"),
    )

    payment_application_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                        default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    payment_receipt_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("payment_receipts.payment_receipt_id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(64),
                                             ForeignKey("customers.customer_id"),
                                             index=True)
    invoice_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(128))
    invoice_date: Mapped[date] = mapped_column(Date)
    #: When it was contractually due. Absent on some invoices; a missing due
    #: date makes "days late" unanswerable, never zero.
    invoice_due_date: Mapped[Optional[date]] = mapped_column(Date)
    paid_on: Mapped[date] = mapped_column(Date, index=True)
    amount_applied: Mapped[Any] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SalesOrderDoc(Base):
    """An order from a customer — demand promised, not yet invoiced.

    The mirror of ``PurchaseOrderDoc``, and deliberately the same shape: one is
    what we promised a supplier, the other what a customer promised us and what
    we promised to ship. Neither is an accounting entry. Both change what the
    business is committed to *before* anything reaches the ledger, which is
    exactly the gap between an ERP's view and a business's.

    Header grain. The line-level split of an open order answers questions this
    does not ask, and would cost one API call per order to obtain.
    """

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_sales_order_source"),
    )

    sales_order_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    customer_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                       ForeignKey("customers.customer_id"),
                                                       index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    #: When we said we would ship. Blank on plenty of orders, which makes "late
    #: against promise" unanswerable for them — reported as such rather than
    #: replaced with an assumed lead time.
    expected_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(48), default="")
    #: Zoho tracks these separately, and they are genuinely different facts: an
    #: order can be fully invoiced and unshipped, or shipped and unbilled. One
    #: combined "fulfilled" flag would lose the distinction that matters to
    #: cash on one side and to service on the other.
    invoiced_status: Mapped[Optional[str]] = mapped_column(String(48))
    shipped_status: Mapped[Optional[str]] = mapped_column(String(48))
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    salesperson_external_id: Mapped[Optional[str]] = mapped_column(String(64))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class QuoteDoc(Base):
    """A quote as an ERP raised it — what was offered, and how the ERP says it
    ended.

    The demand-side document the platform has never read. ``SalesOrderDoc`` is
    what a customer committed to; this is everything that was *offered*,
    including the majority nobody ordered. Without it a win rate has no
    denominator — the invoiced side is visible and the declined side is not, so
    every rate computed from what exists is computed over the winners.

    **Named ``erp_quotes``, not ``quote_documents``.** That name belongs to a
    table pointing the other way — ``QuoteDocument`` records a quote *this
    platform wrote into* a source system, and it landed on main while this was
    on a branch. Two tables called "quote document" in opposite directions is
    exactly the collision the merge found, and the released one keeps the name.
    ``erp_quotes`` also says the direction out loud, which the old name did not.

    Header grain, like ``SalesOrderDoc`` and for the same reason: the line-level
    split would cost one API call per quote and answers a question this does not
    ask.

    **Two status columns, and both earn their place.** ``source_status`` is the
    ERP's own word carried verbatim, never mapped on the way in; ``outcome`` is
    this platform's classification of it, produced by
    ``ingestion.normalize.classify_outcome``. Keeping only the first would put
    the WON/LOST mapping in every reader that ever asks, and the third copy of
    that mapping is the one that reads ``expired`` as a loss. Keeping only the
    second would make the classification unauditable — a GROUP BY on the pair is
    what shows which statuses fell through and why. ``outcome`` is NOT NULL with
    a default of ``UNRECORDED`` rather than nullable, because a nullable
    classification invites a ``COALESCE`` at the point of reading and silence
    would start meaning whatever the last reader chose.

    **Derived, and rewritten wholesale.** ``upsert_quote_document`` assigns every
    column below from the payload on every sync, unconditionally — so a
    human-supplied fact stored here would survive exactly until the next pull,
    the same trap ``VendorPaymentTerm`` exists to avoid on the vendor side. That
    is why there is no ``loss_reason``, no ``lost_to`` and no ``note`` column
    here, and why there is no endpoint that writes this table: why a quote was
    lost is a human fact, it lives on ``quote_outcomes``, which no sync opens,
    and the two are joined by ``QuoteOutcome.quote_document_ref`` holding this
    row's ``external_ref``. A *value* pointer, deliberately — ``DELETE FROM
    quote_documents`` followed by a full re-sync re-mints every
    ``quote_document_id`` here and every human pointer still resolves, which is
    what makes the harshest rebuild safe. ``PaymentApplication.invoice_external_ref``
    is written the same way for the same reason.

    No cost, no margin, no unit economics, and no count or flag that answers a
    margin question. ``total`` is the quote's own selling total — the number
    that was put in front of the customer — so there is no column here a role
    projection would have to withhold, and therefore none that can be forgotten.
    Currency is refused at the ingestion seam like every other document table
    rather than stored.
    """

    __tablename__ = "erp_quotes"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_erp_quote_source"),
        Index("ix_erp_quotes_org_outcome", "organization_id", "outcome"),
        Index("ix_erp_quotes_org_date", "organization_id", "date"),
    )

    quote_document_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: The id the ERP gave this quote. Also the value ``quote_outcomes`` points
    #: at, which is why it is indexed on its own as well as inside the source
    #: uniqueness constraint.
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    #: The ERP's ``reference_number`` — usually the customer's own enquiry or RFQ
    #: number, typed in by whoever raised the quote. The only string on the row
    #: that points back at inbound demand, which is why it is indexed.
    source_reference: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                       ForeignKey("customers.customer_id"),
                                                       index=True)
    #: The ERP's own ``customer_name``, kept even when ``customer_id`` resolves.
    #: A quote to a customer the contact pull did not return is still a quote,
    #: and dropping it would lose exactly the rows worth chasing.
    customer_ref: Mapped[str] = mapped_column(String(255), default="")
    date: Mapped[date] = mapped_column(Date, index=True)
    #: When the offer lapses. Stored rather than derived from an assumed
    #: validity, because "sent last week, awaiting a reply" and "expired in March
    #: and nobody knows" are the two halves of the unrecorded pile and this is
    #: the only field that separates them. Whether a quote is *past* expiry is
    #: computed on read against the day it is asked, never stored: that answer
    #: changes daily and a frozen one is wrong by tomorrow.
    expires_on: Mapped[Optional[date]] = mapped_column(Date)
    #: The ERP's own word for the state of this quote, verbatim and unmapped.
    source_status: Mapped[str] = mapped_column(String(48), default="")
    #: A ``QuoteDocOutcome``: WON, LOST or UNRECORDED. Silence is UNRECORDED and
    #: never LOST — ``expired`` spans "nobody worked it", "the customer never
    #: answered" and "we lost it to a competitor", and only one of those is a
    #: loss. See ``classify_outcome`` for the two positive allowlists.
    outcome: Mapped[str] = mapped_column(String(16), default="UNRECORDED")
    #: The date the ERP recorded the decision on. Non-null whenever ``outcome``
    #: is not UNRECORDED, because a decided quote with no date is not usable as
    #: evidence — ``DecidedQuote.decided_on`` is a required date and the readers
    #: already skip an undated decision. The classifier refuses to call such a
    #: quote decided at all, and the sync counts how often that happens rather
    #: than letting it pass silently.
    decided_on: Mapped[Optional[date]] = mapped_column(Date)
    #: The quote's own selling total. Not a cost, not a margin. NULL where the
    #: ERP gave none, and a NULL is excluded from a value sum and counted, never
    #: read as zero.
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    salesperson_external_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: When the customer opened it, where the ERP tracks that. NULL means nobody
    #: has opened it, and that is read as a fact rather than as missing data —
    #: which is why normalisation rejects an unplaceable stamp instead of nulling
    #: it. It is deliberately *not* an input to the outcome classification: a
    #: customer reading a quote is not a customer deciding on one.
    client_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: The business's own taxonomy on the quote — Zoho's ``cf_quote_type``,
    #: ``cf_pricing_type``, ``cf_procurement_type`` and the branch it was raised
    #: at — verbatim, keys and values as the source wrote them. A JSON bag rather
    #: than typed columns because one tenant's ERP configuration is not a schema
    #: every connector has to share, and an absent key stays absent: "not set" is
    #: not a category.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class BillDoc(Base):
    """A supplier bill's payable terms — the header the cost lines came from.

    Bills have been read since the first sync, but only for what the stock
    cost: each one was normalised into ``CostRecord`` rows at line grain and
    the header thrown away. So the platform knew what it had paid per insert
    and nothing at all about what it still *owed*, which is why accounts
    payable and working capital had no source.

    Header grain, deliberately separate from ``CostRecord`` rather than
    columns on it: ``due_date`` and ``balance`` are facts about one document,
    and copying them onto forty cost lines would make "what is outstanding"
    a de-duplication problem instead of a sum.

    Written from the same payload the cost pull already fetches — no extra API
    call, no extra scope. ``balance`` is what Zoho says is still owed; it is
    never derived from ``total`` minus payments read elsewhere, because a
    credit note against the bill would make that subtraction wrong.
    """

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_bill_source"),
        Index("ix_bill_org_due", "organization_id", "due_date"),
    )

    bill_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    vendor_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                     ForeignKey("vendors.vendor_id"),
                                                     index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    #: When it falls due. Blank on bills raised without terms, which makes them
    #: unageable — reported as such rather than assumed to be due on receipt.
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(48), default="")
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    balance: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class InvoiceDoc(Base):
    """An invoice at header grain — what a customer owes, and by when.

    The mirror of ``Bill``, and for the same reason. Invoices were normalised
    into ``SalesTxn`` rows at line grain and the header thrown away, so the
    platform knew what it had sold per line and nothing at all about what was
    still *owed*. ``PaymentApplication`` covers only invoices that have been
    paid, which is precisely the wrong half for a collections question.

    Header grain, deliberately separate from ``SalesTxn`` rather than columns on
    it: ``due_date`` and ``balance`` are facts about one document, and copying
    them onto forty revenue lines would make "what is outstanding" a
    de-duplication problem instead of a sum.

    Written from the same payload the invoice pull already fetches — no extra
    API call, no extra scope. ``balance`` is what Zoho says is still owed; it is
    never derived from ``total`` minus receipts read elsewhere, because a credit
    note against the invoice would make that subtraction wrong in the direction
    that gets a customer chased for money they do not owe.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_invoice_source"),
        Index("ix_invoice_org_due", "organization_id", "due_date"),
    )

    invoice_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("customers.customer_id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    #: When it falls due. Blank on invoices raised without terms, which makes
    #: them unageable — reported as such rather than assumed due on issue.
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(48), default="")
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    balance: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class InvoiceSalesOrderLink(Base):
    """Which customer orders one invoice bills against.

    The invoice header carried no order reference at all, so the platform could
    say how long a customer took to pay and nothing about how long *we* took to
    turn their order into an invoice — which is the half of the order-to-cash
    cycle this business can actually do something about.

    **A link table because the relationship is genuinely many-to-many, and both
    directions occur in this book.** Partial invoicing is normal here, so one
    order produces several invoices (``HYD/FY27/SO-399`` appears on both
    ``INV-696`` and ``INV-663``); and one invoice can bill against several
    orders, which is why Zoho's invoice payload carries a ``salesorders``
    *array* beside the scalar ``salesorder_id``. A nullable column on
    ``invoices`` would hold the scalar and silently drop the rest, and the rows
    it dropped would be exactly the consolidated invoices whose cycle time is
    most worth looking at.

    ``is_primary`` records which one Zoho's scalar named. Kept so the array can
    be the truth without the scalar becoming unrecoverable — a reader who wants
    Zoho's own primary can still have it, and nobody has to guess whether the
    scalar was read at all.

    Keyed on ``external_ref`` at both ends rather than on ``invoice_id`` and
    ``sales_order_id``, exactly as ``PaymentApplication`` and
    ``CreditNoteApplication`` are: an invoice can name an order raised before
    the sync window opens, and a foreign key would drop that link rather than
    record it as an order whose date this platform does not hold. The two are
    different facts and only one of them is a defect.

    The order's *date* is deliberately not copied here. It lives on
    ``sales_orders`` and is joined; a second copy would be a second answer to
    "when was this ordered", and a link whose order is outside the window is
    reported as an unknown lag rather than a zero one.
    """

    __tablename__ = "invoice_sales_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "invoice_external_ref", "sales_order_external_ref",
                         name="uq_invoice_sales_order_source"),
        Index("ix_invoice_so_org_invoice",
              "organization_id", "invoice_external_ref"),
        Index("ix_invoice_so_org_order",
              "organization_id", "sales_order_external_ref"),
    )

    invoice_sales_order_id: Mapped[str] = mapped_column(String(64),
                                                        primary_key=True,
                                                        default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    invoice_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    sales_order_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    #: The order number as the invoice stated it, so a row is readable without
    #: the order itself having been synced.
    sales_order_number: Mapped[Optional[str]] = mapped_column(String(128))
    #: Zoho's scalar ``salesorder_id`` named this one. Never used to *choose* an
    #: order for the measurement — see ``commercial/insight/order_to_cash``.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Location(Base):
    """A place the business trades from — Zoho's "location", the book's "branch".

    The invoice payload carries both names for one id (``location_id`` and
    ``branch_id`` hold the same value), so this table is named for the concept
    rather than for either field.

    **These are not labels.** On this book Head Office and the Bangalore branch
    carry *different GST registrations*, which is what makes "which branch
    earned this" a question about a trading entity rather than a reporting
    preference. A third entry — a godown — is a child of head office and is not
    a branch at all, which is why the nesting below is stored rather than
    flattened.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_location_source"),
    )

    location_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                             default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    #: Zoho's own kind — ``general`` trades, ``line_item_only`` is a store that
    #: may appear on a document line without being a branch. Stored raw and
    #: judged at read time: which kinds count as a branch is policy, and a value
    #: rewritten at sync time could never be re-read under a corrected rule.
    kind: Mapped[Optional[str]] = mapped_column(String(48))
    #: Parent, where Zoho nests one location under another. A roll-up that
    #: ignored this would count a godown's stock twice — once as itself and once
    #: inside the head office that contains it.
    parent_external_ref: Mapped[Optional[str]] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The GSTIN registered here, where there is one.
    tax_reg_no: Mapped[Optional[str]] = mapped_column(String(32))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class StockLocationSnapshot(Base):
    """What one item held at one location, on one day.

    **Deliberately a separate table from ``StockSnapshot``, not columns on it.**
    That one is organization-grain — one row per item per day, and its unique
    constraint says so. Adding a location column would turn each of those rows
    into one row per location, and every reader that sums it without knowing
    would silently multiply its answer by the number of branches. Two grains,
    two tables; the same reason ``InvoiceDoc`` sits beside ``SalesTxn`` rather
    than being copied onto it.

    The organization-grain row remains the total and remains authoritative.
    Nothing here is meant to replace it, and a reader wanting "what do we hold"
    should still ask ``StockSnapshot``.

    ``asset_value`` is what Zoho values the holding at, and it is **cost** —
    manager scope only, exactly like ``StockSnapshot.purchase_rate``.
    """

    __tablename__ = "stock_location_snapshots"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "product_id", "location_external_ref", "as_of",
                         name="uq_stock_loc_source"),
        Index("ix_stock_loc_org_asof", "organization_id", "as_of"),
    )

    stock_location_snapshot_id: Mapped[str] = mapped_column(String(64),
                                                            primary_key=True,
                                                            default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64),
                                            ForeignKey("products.product_id"),
                                            index=True)
    #: Zoho's location id, not this platform's. Kept as the external reference
    #: rather than a foreign key to ``locations`` because stock can be reported
    #: against a location the location pull has not returned — an inactive one,
    #: or one added between phases — and dropping the row would understate the
    #: shelf in order to say where it sat.
    location_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    on_hand: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    available: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Zoho's valuation of this location's holding. Cost — manager scope only.
    asset_value: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreditNoteDoc(Base):
    """A credit note at header grain — what was given back, and to whom.

    The mirror of ``InvoiceDoc``. Credit notes were not read at all until this
    was added, and the gap was invisible precisely because it did *not* corrupt
    today's numbers: Zoho nets applied credit into ``InvoiceDoc.balance``, so
    the receivables fold has always been correct about the present.

    What it could not do is describe the past. Reconstructing what a customer
    owed on a date gone by means invoices raised, minus receipts applied, minus
    credit applied — and the third term had no source. Without it a
    reconstructed receivable is overstated by exactly the credit issued, in the
    direction that flatters collection performance.

    **This is not a second outstanding balance.** ``state/reducers/receivables``
    reads ``InvoiceDoc.balance`` and must keep doing so; folding these rows into
    it as well would subtract the same credit twice. Nothing here belongs in the
    current-position fold.
    """

    __tablename__ = "credit_notes"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_credit_note_source"),
        Index("ix_credit_note_org_date", "organization_id", "date"),
    )

    credit_note_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("customers.customer_id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(48), default="")
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Credit raised but not yet set against any invoice. Read from Zoho, never
    #: derived as total minus the applications below — a refund against the
    #: credit note would make that subtraction overstate what is still available.
    balance: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class CreditNoteApplication(Base):
    """One credit note set against one invoice — the row a past balance needs.

    The exact analogue of ``PaymentApplication``, and stored at the same grain
    and for the same reason: one credit note may be spread across several
    invoices, and each application is its own dated fact. The invoice's own date
    is stored here rather than joined to ``invoices``, because a credit note can
    be applied to an invoice raised before the sync window begins and a join
    would silently drop the oldest positions — the ones a receivables history
    most needs.
    """

    __tablename__ = "credit_note_applications"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_credit_note_application_source"),
        Index("ix_credit_note_app_org_invoice",
              "organization_id", "invoice_external_ref"),
    )

    credit_note_application_id: Mapped[str] = mapped_column(String(64),
                                                            primary_key=True,
                                                            default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    credit_note_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("credit_notes.credit_note_id"), index=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("customers.customer_id"), index=True)
    invoice_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(128))
    #: Absent where Zoho did not state it. A missing invoice date makes this
    #: application unplaceable on a receivables timeline — reported as such by
    #: whatever reads it, never defaulted to the application date.
    invoice_date: Mapped[Optional[date]] = mapped_column(Date)
    applied_on: Mapped[date] = mapped_column(Date, index=True)
    amount_applied: Mapped[Any] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VendorCreditDoc(Base):
    """A vendor credit at header grain — what a supplier gave back, and by whom.

    The buy-side mirror of ``CreditNoteDoc``, and the gap it closes is the one
    ``11-procurement.md`` measured: ``rg -ic "vendor.?credit"`` across
    ``backend/`` returned nothing while the SLS book held 13 of them, one worth
    ₹4.83 lakh of returned stock. None of it reduced a line's cost, a
    principal's slab base, or a supplier's credit-note rate, because none of it
    was read.

    **Store-only, on purpose.** ``CostRecord`` still has exactly one source —
    the bill line — and nothing here writes to it. Two distinct effects wait on
    this table and neither is taken here: a return ought to reduce the slab
    base, and a bill-specific price credit ought to reduce the cost of the lines
    it corrects. The second moves every per-line margin in the platform and
    turns on the accrual-treatment question ``11-procurement.md`` §1 refers to
    an accountant. It gets its own change and its own review.

    **Not a second payable balance.** ``BillDoc.balance`` is what is owed and
    Zoho has already netted applied credit out of it; folding these rows in as
    well would subtract the same credit twice. Whatever reads this reads it
    directly, the same way ``CreditNoteDoc`` is read.
    """

    __tablename__ = "vendor_credits"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_vendor_credit_source"),
        Index("ix_vendor_credit_org_date", "organization_id", "date"),
    )

    vendor_credit_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                  default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's book.
    # The same triple every other synced document carries: an external
    # reference is unique only inside the system that issued it, and only
    # inside one company of that system.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    vendor_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("vendors.vendor_id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(48), default="")
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: Credit raised but not yet set against any bill. Read from Zoho, never
    #: derived as total minus the applications below — a refund against the
    #: credit would make that subtraction overstate what is still available.
    balance: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class VendorCreditApplication(Base):
    """One vendor credit set against one bill.

    The grain the two deferred effects will need: a bill-specific price credit
    is placed by the bill it names, and a return is attributed to a principal by
    the credit it hangs off. One credit is routinely spread across several bills
    — the Kennametal document above covers eight — so a header total alone
    cannot say which purchase was corrected.

    **Line attribution is deliberately absent, because it would be an
    inference.** The credits in these books carry ``bill_item_id: ""`` — naming
    the item but not the bill line it corrects — and the "Rate Difference"
    credit names no item at all. Anything finer than this bill is guesswork
    dressed as a fact, and ``11-procurement.md`` says so in as many words.

    **There is no application date, and it is missing on purpose.** See
    ``VendorCreditApplicationIn``: Zoho's ``bills_credited`` carries one
    unlabelled ``date`` whose values match the bills rather than the days the
    document's own system comments record the credit being applied. A column
    named ``applied_on`` holding a bill's date is worse than no column.
    """

    __tablename__ = "vendor_credit_applications"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_vendor_credit_application_source"),
        Index("ix_vendor_credit_app_org_bill",
              "organization_id", "bill_external_ref"),
    )

    vendor_credit_application_id: Mapped[str] = mapped_column(String(64),
                                                              primary_key=True,
                                                              default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    vendor_credit_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("vendor_credits.vendor_credit_id"), index=True)
    vendor_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("vendors.vendor_id"), index=True)
    bill_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    bill_number: Mapped[Optional[str]] = mapped_column(String(128))
    amount_applied: Mapped[Any] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VendorPaymentDoc(Base):
    """Money out, at the payment grain.

    ``PaymentReceipt`` has recorded money in since the cash screen was built.
    Receipts alone are not cash — they are revenue collected — so liquidity and
    working capital could not be computed from one side of the ledger. This is
    the other side.

    ``BillPaymentApplication`` is the application table this docstring used to
    say would be "added when the first reader exists". The reader exists: the
    cash projection places money out on the dates our bills claim, and had no
    way to know that this book settles them a fortnight after those dates.
    """

    __tablename__ = "vendor_payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_vendor_payment_source"),
    )

    vendor_payment_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    vendor_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                     ForeignKey("vendors.vendor_id"),
                                                     index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Any] = mapped_column(Numeric(18, 4))
    mode: Mapped[Optional[str]] = mapped_column(String(48))
    reference: Mapped[Optional[str]] = mapped_column(String(128))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BillPaymentApplication(Base):
    """One payment out against one bill — the row days-to-pay is computed from.

    The mirror of ``PaymentApplication``, at the same grain and for the same
    reason: one bank transfer settling ten bills is ten observations, each with
    its own bill date, and measuring the payment instead would give a book that
    batches its remittances a single flattering data point.

    The bill's own date and due date are stored here rather than joined to
    ``bills``, exactly as on the receivable side. A bill can predate the sync
    window — a payment made today may settle one from before the platform's
    history starts — and a join would silently drop precisely the slowest
    settlements, which are the ones a supplier is already unhappy about.

    ``vendor_id`` is nullable where ``PaymentApplication.customer_id`` is not,
    and the asymmetry is real rather than an oversight: an inbound payment from
    a customer the contact pull never returned is skipped at ingest, while a
    payment *we* made is a fact about our own bank account whether or not the
    supplier resolved. It is kept, and left out of the per-vendor behaviour with
    the count reported, rather than dropped or filed under a placeholder.
    """

    __tablename__ = "bill_payment_applications"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_bill_payment_application_source"),
        Index("ix_bill_payment_app_org_bill", "organization_id", "bill_external_ref"),
    )

    bill_payment_application_id: Mapped[str] = mapped_column(String(64),
                                                             primary_key=True,
                                                             default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    vendor_payment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("vendor_payments.vendor_payment_id"), index=True)
    vendor_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                     ForeignKey("vendors.vendor_id"),
                                                     index=True)
    bill_external_ref: Mapped[str] = mapped_column(String(128), index=True)
    bill_number: Mapped[Optional[str]] = mapped_column(String(128))
    bill_date: Mapped[date] = mapped_column(Date)
    #: When it was contractually due. Absent on some bills; a missing due date
    #: makes "days late" unanswerable, never zero.
    bill_due_date: Mapped[Optional[date]] = mapped_column(Date)
    paid_on: Mapped[date] = mapped_column(Date, index=True)
    amount_applied: Mapped[Any] = mapped_column(Numeric(18, 4))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PurchaseOrderDoc(Base):
    """An order placed on a supplier, and how much of it has arrived.

    Header grain only. The line detail matters for a receiving screen and this
    is not one — the questions here are "what is still outstanding, with whom,
    and for how long", all of which the header answers.
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "connector", "connection_id", "external_ref",
                         name="uq_purchase_order_source"),
        Index("ix_po_org_date", "organization_id", "date"),
    )

    purchase_order_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    # Which system this document came from, and which connected company's
    # book. The same triple the master tables carry: an external reference is
    # unique only inside the system that issued it, and only inside one company
    # of that system. Nullable because a row written before this existed cannot
    # be attributed after the fact — see the migration for why nothing is
    # backfilled.
    connector: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(128))
    vendor_id: Mapped[Optional[str]] = mapped_column(String(64),
                                                     ForeignKey("vendors.vendor_id"),
                                                     index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    #: What the supplier promised. Blank on most of this book's orders in
    #: practice, which makes "late against promise" unanswerable — and that is
    #: reported rather than replaced with an assumed lead time.
    expected_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(48), default="")
    received_status: Mapped[Optional[str]] = mapped_column(String(48))
    ordered_qty: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    pending_qty: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    total: Mapped[Optional[Any]] = mapped_column(Numeric(18, 4))
    #: When the order was fully received, where Zoho records it. The only basis
    #: for an *actual* lead time; absent means the order is still open or the
    #: receipt was never logged, and those are not the same thing.
    received_on: Mapped[Optional[date]] = mapped_column(Date)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class BusinessEvent(Base):
    """One thing that happened to the business, as this platform read it.

    Append-only and ordered. It is **derived, not canonical**: Zoho is the
    system of record and the platform never writes back, so an event is our
    *reading* of a document, and an accountant correcting an invoice in Zoho
    must win. Everything here is rebuildable from a complete re-sync, which is
    what keeps replay honest — if the log and Zoho ever disagree, Zoho is right
    and the log is re-derived, never reconciled by hand.

    **Nothing mutates.** A document edited upstream does not update its events;
    the old ones are stamped ``superseded_at`` and the new reading is appended
    after them. So the log records what the platform believed *and when it
    stopped believing it*, which is the whole point of keeping one.

    ``superseded_at`` rather than a ``superseded_by`` pointer, deliberately: a
    re-read replaces a document's events with a *set* of new ones, not one for
    one — a nine-line invoice edited down to seven has no honest pairing. The
    replacements are the live events sharing the same document reference, and a
    single pointer would have to lie about which.

    Two times, because they are different questions. ``occurred_on`` is when
    the business fact happened — the invoice date, what every commercial
    calculation must use. ``recorded_at`` is when this platform learned it.
    Collapsing them would make a bill entered three weeks late look like it
    happened three weeks late.

    ``seq`` is a single global sequence rather than one per organization. It is
    still a total order within any organization, it is the only thing that
    survives two documents sharing a date, and one counter cannot drift from
    another the way two can.
    """

    __tablename__ = "business_events"
    __table_args__ = (
        Index("ix_event_org_seq", "organization_id", "seq"),
        # The supersede lookup: every re-read of a document asks this exact
        # question before appending anything.
        Index("ix_event_org_doc", "organization_id", "source_doc_type",
              "source_doc_id"),
        Index("ix_event_org_type_on", "organization_id", "event_type",
              "occurred_on"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Which connected company this reading came from. Same provenance triple
    #: the masters carry — an event with no source cannot be re-derived, and a
    #: log that cannot be re-derived is not replayable.
    connector: Mapped[Optional[str]] = mapped_column(String(32))
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: The document this was read from, and the stamp it was read at. The same
    #: triple ``IngestedDocument`` resumes on, so idempotency here reuses a
    #: mechanism that already works rather than inventing a second one.
    source_doc_type: Mapped[str] = mapped_column(String(32))
    source_doc_id: Mapped[str] = mapped_column(String(128))
    source_line_id: Mapped[Optional[str]] = mapped_column(String(128))
    source_modified_at: Mapped[str] = mapped_column(String(64), default="")
    #: The normalised DTO, JSON-encoded. Decimals and dates travel as strings
    #: so replay parses them back exactly — a float round-trip would put binary
    #: noise into a figure the whole platform is meant to reproduce.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class BusinessState(Base):
    """One state, for one key, as of one day.

    A *projection*, not a fact: every row is folded from ``BusinessEvent`` rows
    and can be dropped and rebuilt. What it buys is the thing the platform
    could not do before — answer "what was this on the 31st of March" without
    re-scanning the whole history, and without each screen recomputing its own
    version of the same number in its own module.

    ``as_of`` is part of the key, so a state is a series rather than a current
    value that overwrites its own history. That is the whole reason inventory
    value in March is answerable at all.

    ``thresholds_version`` is stamped from ``CommercialThresholds`` by the
    caller, the same way every computed row in this schema is. Without it a
    state row computed under one policy is indistinguishable from one computed
    under another, and a changed threshold makes every past number
    unexplainable.

    ``value`` is JSON with money as strings and dates as ISO — the same
    contract as an event payload, for the same reason: a float round-trip puts
    binary noise into a figure the platform is meant to reproduce exactly.
    """

    __tablename__ = "business_states"
    __table_args__ = (
        UniqueConstraint("organization_id", "state", "key", "as_of",
                         name="uq_state_org_state_key_asof"),
        Index("ix_state_org_state_asof", "organization_id", "state", "as_of"),
    )

    business_state_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(48), index=True)
    #: The thing this state is *about* — a product id, a party id, or a
    #: reducer's composite of them (``vendor:product``,
    #: ``customer:product:2026-03``). Local ids, never external ones: two
    #: connected companies can number from one, and a state keyed on an
    #: external id would silently merge them. 160 because a composite of two
    #: 64-char ids plus a month bucket is 137 — String(64) fit only the single
    #: ids, which SQLite never enforced and Postgres rejected on first
    #: contact.
    key: Mapped[str] = mapped_column(String(160), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: How many live events were folded into this row. Not decoration: a state
    #: computed from three events and one computed from three hundred deserve
    #: different confidence, and a row with zero is a bug rather than a zero.
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    thresholds_version: Mapped[str] = mapped_column(
        String(64), default="", info={"policy_stamp": "commercial"})
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StateTransition(Base):
    """One event's effect on one state key — the traceability hop.

    This is what makes "why does this number exist" answerable: from a state
    row, every event that moved it, and by how much. Without it a projection is
    a number with a provenance story nobody can check.

    Replaced per (state, as_of) on each rebuild rather than appended forever.
    The event log is the permanent record; this is the working of one fold, and
    keeping every historical *rebuild* of the same fold would grow without
    bound while answering a question nobody asks — a superseded fold is
    reproducible from the events, which is the point of keeping those.
    """

    __tablename__ = "state_transitions"
    __table_args__ = (
        Index("ix_transition_org_state_key", "organization_id", "state",
              "as_of", "key"),
        Index("ix_transition_event", "organization_id", "event_seq"),
    )

    state_transition_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                     default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The event that caused it. Not a foreign key: events are pruned by a
    #: complete re-sync while a rebuild is mid-flight in another process, and a
    #: constraint would turn a re-derivable projection into a blocking failure.
    event_seq: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(48), index=True)
    #: Same width as BusinessState.key, for the same composite-key reason.
    key: Mapped[str] = mapped_column(String(160), index=True)
    #: Which fold this working belongs to. Two builds at different ``as_of``
    #: dates are two different arithmetics over the same events, and a
    #: transition that did not say which would explain the wrong one.
    as_of: Mapped[date] = mapped_column(Date, index=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    #: The field changes this event made, as ``[[op, field, value], …]``.
    changes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConfirmedCodeMapping(Base):
    """"This customer's part number means that manufacturer product."

    The fact a salesperson establishes once and should never be asked again.
    pie-parser can resolve a scoped code authoritatively when a confirmed
    mapping exists — but its own store is a CSV inside the engine repository,
    which ships empty and is shared by everyone who runs the engine. A
    confirmation is made by a person, about one organization's customer, so it
    belongs here and is handed to the engine at resolution time.

    Scoped to a ``CustomerIdentity``, never to a connector's customer row: the
    same real customer reached through two connected Zoho companies quotes the
    same part number, and filing the fact twice would let the two answers drift.

    Superseded rather than mutated. A mapping that was true and was later
    corrected is how you explain a quote sent last March, and overwriting the
    row in place destroys exactly that.
    """

    __tablename__ = "confirmed_code_mappings"
    __table_args__ = (
        Index("ix_confirmed_code_lookup",
              "organization_id", "identity_id", "code", "active"),
    )

    mapping_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The customer's cross-connector identity — the namespace this code lives in.
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The customer's own code, normalized the way pie-parser normalizes an
    #: identity key: trimmed and upper-cased, with internal punctuation intact,
    #: because separators can be meaningful in a part number.
    code: Mapped[str] = mapped_column(String(128))
    #: The manufacturer record it resolves to (an MM#).
    target_record_id: Mapped[str] = mapped_column(String(64))
    #: pie-parser's RelationshipType. SAME_PRODUCT is the only one that resolves
    #: authoritatively; the rest are recorded but still ask for review.
    relationship: Mapped[str] = mapped_column(String(32), default="SAME_PRODUCT")
    #: Where the confirmation came from, e.g. "quote q1 line l3" — so a wrong
    #: mapping can be traced to the moment somebody made it.
    source_ref: Mapped[str] = mapped_column(String(255), default="")
    confirmed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: False once superseded by a later confirmation for the same code.
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    superseded_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 index=True)


class QueuedMessage(Base):
    """One unit of background work, durable enough to survive the process.

    Background work has existed here since the first sync took minutes: a
    thread is started, the ``SyncRun`` row records what it is doing, and the
    screen polls that row. What a thread cannot do is outlive its process. A
    run that is QUEUED when the container is replaced — a deploy, an OOM, a
    host moving — is simply never done; ten minutes later the reaper marks its
    row stale and the work has silently evaporated. That is the gap this table
    closes: the *request* to do the work is committed before anything starts,
    so a worker in the next process can pick it up.

    Deliberately a table rather than a broker. The queue depths here are one
    message per organization per sync cadence, the database is already the
    thing every process shares, and a broker would be one more service to run,
    secure and reason about for a workload that fits in a single indexed query. What
    the table has to get right is the same short list any queue does:

    * **Claiming is atomic.** A message moves PENDING -> CLAIMED with a
      conditional UPDATE, so two workers racing for it produce one winner and
      one zero-row update rather than two runs of the same job. This is what
      makes multiple processes safe — which the thread dispatch never was, and
      said so.
    * **A dead worker releases its work.** ``heartbeat_at`` is the same
      mechanism ``SyncRun`` uses: a CLAIMED message whose worker stopped
      reporting is returned to PENDING rather than held forever by a process
      that no longer exists.
    * **Failure is bounded and visible.** Each attempt is counted and retried
      with a growing delay; a message that exhausts its attempts becomes
      DEAD_LETTER with its last error, which is a row an operator can read.
      It is never silently dropped.
    * **A duplicate is refused, not run twice.** ``dedupe_key`` names the work
      rather than the request, so "sync organization X" enqueued twice while
      the first is still pending is one message.

    Rows are kept after completion: a queue with no history cannot answer "did
    that run, and when". They are derived state — deleting them loses the
    account of what ran and breaks nothing that computes a number.
    """

    __tablename__ = "queued_messages"
    __table_args__ = (
        # The claim query, exactly: the oldest available message on a topic.
        Index("ix_queued_messages_claimable", "status", "available_at"),
        # The dedupe lookup, which runs on every enqueue.
        Index("ix_queued_messages_dedupe", "dedupe_key", "status"),
    )

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: What kind of work this is — the key a handler is registered under.
    topic: Mapped[str] = mapped_column(String(64), index=True)
    #: Everything the handler needs, as JSON. Values only: a payload holding an
    #: ORM object would be a reference into a session that is long closed by the
    #: time the message runs, possibly in another process.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: The tenant this work belongs to, where it belongs to one. Null for
    #: platform-wide work. Carried so a queue depth can be read per
    #: organization and so one tenant's backlog is legible as theirs.
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    #: PENDING | CLAIMED | DONE | DEAD_LETTER
    status: Mapped[str] = mapped_column(String(16), index=True, default="PENDING")
    #: Names the *work*, not the request: while a message with this key is
    #: PENDING or CLAIMED, enqueueing it again returns the existing one.
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(191))
    #: Not before this instant. Both the delay of a scheduled message and the
    #: backoff of a retry are expressed here, so the claim query needs one
    #: condition rather than two notions of "later".
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   default=_now, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    #: Which worker holds it, for a human reading the table during an incident.
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: Touched while the handler runs. A CLAIMED row whose heartbeat has gone
    #: cold is a dead worker's, not a slow one's — same rule as ``SyncRun``.
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: Why the last attempt failed, kept even after a later attempt succeeds:
    #: a message that needed three tries is worth knowing about.
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProcessLease(Base):
    """Which process is currently the one doing a named job.

    One row per named responsibility — ``sync-scheduler`` is the only one today.
    A process claims it, renews it while it works, and the row's expiry is what
    hands it on when that process dies.

    It exists because "only one process does this" was a property of the
    deployment rather than of the code: ``ingestion/scheduler`` reasoned from
    "this deployment is one uvicorn process", while the image has shipped
    ``--workers 2`` throughout. Two ticking threads, starting together, both
    deciding an organization is due, and no constraint behind ``start_sync``'s
    read of the active runs — so both queue a pull of the same books.

    Derived and disposable: deleting this table costs the schedule a couple of
    minutes of confusion and nothing else. Nothing computes from it, and no
    business fact lives here.
    """

    __tablename__ = "process_leases"

    #: The responsibility, not the holder. "sync-scheduler".
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Which process holds it: "<pid>:<short random>", so an incident has a name
    #: to grep for rather than a boolean.
    holder: Mapped[str] = mapped_column(String(128))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Touched every time the holder renews — the tell that a holder is alive
    #: rather than merely recorded.
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: After this instant anybody may take it. A lease rather than a lock: one a
    #: dead process keeps forever is the failure this shape exists to avoid.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

# ── Inbound demand (canonical; a re-sync cannot rebuild it) ──────────────────
#
# Everything above this line is either a projection of Zoho or something
# computed from one. These two tables are neither: an enquiry arrives as a
# WhatsApp message or a PDF and exists nowhere else, so a complete re-sync
# rebuilds nothing here. That is why they are not in ``state/``, whose whole
# contract is "derived, never canonical", and why losing them is data loss
# rather than a recompute.


class InboundLine(Base):
    """One line of one inbound enquiry, exactly as it arrived.

    The instrument three separate things need, which is why it is one table and
    not three:

    * It is the only **coverage denominator that does not condition on
      success**. Every other count in this schema starts from a quote, an order
      or an invoice — a record that exists because something went right — so
      dividing by any of them measures the wrong population. What "we answered
      62% of what was asked of us" needs is a row per *ask*, written before
      anyone knows how it ends.
    * It is the **corpus an RFQ accuracy benchmark reads**. A benchmark over
      cleaned text measures the cleaner.
    * It is the raw material for **unquoted-demand reporting** — the lines that
      went nowhere, which by construction appear in no other table.

    **No normalisation at capture.** ``raw_text`` is the bytes the customer
    sent: their casing, their spelling, their line breaks, their trailing
    whitespace, their mojibake. This is the one rule the whole table rests on,
    and it is the one a well-meaning change breaks first — a strip() at the
    capture boundary looks like hygiene and quietly destroys the corpus, because
    the parser under benchmark is the thing that is *supposed* to cope with the
    mess. Normalise on the way out, per reader, never on the way in.

    **Append-only, and no deduplication.** A row is never updated; there is no
    supersede stamp here because there is nothing to supersede — what arrived,
    arrived. Nor is there a uniqueness key, and that is deliberate rather than
    an omission: the same customer asking for the same part twice in a week is
    two enquiries and must count as two, so no key over the content could be
    correct. A source system that redelivers one message twice is that adapter's
    problem to solve on ``source_ref`` before calling ``enquiry.capture``, not a
    constraint this table can express without lying about repeat demand.

    **Per-tenant, and more sharply than most.** ``organization_id`` scopes every
    row like every other entity (see this module's docstring), but the content
    raises the stakes: ``raw_text`` is a customer's own words, routinely naming
    their project, their end customer, their volumes and their urgency. It is
    the tenant's commercial intelligence, not ours. A cross-tenant aggregate
    over these rows — "what is the market asking for this quarter" — is an
    obvious and genuinely valuable idea, and it **cannot be built today**: it
    would need a consent primitive recording that a tenant agreed to contribute,
    at what granularity and revocably, and no such record exists anywhere in
    this codebase. ``trust/`` holds tenant keys, the encrypted name vault,
    pseudonyms, break-glass and erasure — infrastructure for protecting one
    tenant's data, not for asking permission to share it. So: strictly
    per-tenant, and anyone reaching for the aggregate builds the consent record
    first.

    **No cost, no margin, ever.** Nothing here is economics: it is text, a
    channel, a customer reference and two timestamps. A line's economics belong
    to the quote it became (§1), and a column added here to save a join would
    put cost one table closer to a surface that has no business seeing it.

    ``customer_ref`` is free text, not a ``Customer`` FK. Enquiries arrive from
    people who are not yet customers, from addresses that resolve to nobody, and
    from names spelled the way the sender spells them. Resolving at capture
    means either dropping the unresolvable — which are exactly the lines the
    coverage report exists to count — or writing a guess into a foreign key.
    Resolution is a later, lossy, re-runnable step over what was written here.
    """

    __tablename__ = "inbound_lines"
    __table_args__ = (
        # The export and the reporting window: one tenant's lines, by arrival.
        Index("ix_inbound_lines_org_received", "organization_id", "received_at"),
        # Coverage by route — "how much of what arrives on WhatsApp do we
        # answer" is the question this table was asked for first.
        Index("ix_inbound_lines_org_channel", "organization_id", "channel"),
    )

    inbound_line_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                 default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)

    #: The customer's own words. ``Text``, not ``String(n)``: a pasted RFQ can
    #: be a paragraph, and a truncated corpus is a silently wrong benchmark.
    raw_text: Mapped[str] = mapped_column(Text)
    #: An ``InboundChannel``.
    channel: Mapped[str] = mapped_column(String(16))
    #: Whatever identified the sender — a name, an email address, a phone
    #: number. Verbatim, for the reason the class docstring gives.
    customer_ref: Mapped[str] = mapped_column(String(255), default="")
    #: Where this was read from: a message id, a file name, a portal request id.
    #: Provenance, the same way every other persisted fact here carries it, and
    #: the handle an adapter uses to notice it is redelivering.
    source_ref: Mapped[str] = mapped_column(String(255), default="")

    #: When the customer sent it — the business fact, and what every coverage
    #: window filters on. Supplied by the adapter from the message itself.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_now)
    #: When this platform wrote it down. Separate from ``received_at`` for the
    #: reason ``BusinessEvent`` keeps two: a WhatsApp backlog imported on Friday
    #: is not a Friday of demand.
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_now)


class InboundLineDisposition(Base):
    """How one inbound line ended — and, once, how we used to think it ended.

    Its own table rather than a column on ``InboundLine``, because the
    requirement is that a disposition is **superseded, never mutated**, and a
    column cannot hold two answers. A line marked NO_STOCK on Tuesday and
    QUOTED on Thursday has to keep both readable: the first is what the coverage
    report said that week, and a number that changed with no record of having
    changed is a number nobody can defend.

    The live disposition is the row with ``superseded_at IS NULL``, and the
    partial unique index below is what makes "one live answer per line" a
    database guarantee rather than a convention. It is the
    ``uq_value_event_key_live`` idiom, chosen for the same reason: a plain
    unique constraint would refuse the correction, which is exactly the
    operation supersession exists to allow.

    ``superseded_at`` is the one stamp written to an existing row, and the table
    is still append-only in the sense that matters: the *claim* is immutable,
    and the stamp only records that a newer claim replaced it. This is the rule
    ``BusinessEvent`` and ``ValueEvent`` follow, and there is no second pattern
    for it in this schema.

    No ``superseded_by`` pointer, because here — unlike a document re-read — the
    replacement genuinely is one-for-one, and the pointer would be derivable
    from the line id and the timestamps. One stamp, following the two tables
    that already made this choice.

    Nothing here is economics. NO_PRICE is a coverage outcome, not a price; the
    reason a line had no price is a commercial fact and stays in ``commercial/``
    (§1). ``source_ref`` names the quote or the note the decision came from and
    must not be used to smuggle one back.
    """

    __tablename__ = "inbound_line_dispositions"
    __table_args__ = (
        # One *live* disposition per line. Unique over the live rows only: a
        # correction writes a second row for the same line, and a plain unique
        # constraint would reject the very write this table is shaped around.
        # Both backends support partial indexes (SQLite since 3.8, Postgres
        # always), so this needs no dialect branch beyond naming the predicate
        # twice.
        Index("uq_inbound_line_disposition_live",
              "organization_id", "inbound_line_id",
              unique=True,
              sqlite_where=text("superseded_at IS NULL"),
              postgresql_where=text("superseded_at IS NULL")),
        # The history read: every version for one line, oldest first.
        Index("ix_inbound_line_dispositions_line", "inbound_line_id",
              "recorded_at"),
        # The report read: this tenant's outcomes by kind.
        Index("ix_inbound_line_dispositions_org_disposition",
              "organization_id", "disposition"),
    )

    disposition_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The line this is about. A real FK: a disposition for a line that does not
    #: exist is not history, it is corruption, and the coverage numerator would
    #: quietly exceed its denominator.
    inbound_line_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("inbound_lines.inbound_line_id"), index=True)

    #: A ``LineDisposition``. Terminal — see that enum for why there is no
    #: PENDING member and what the absence of a row means.
    disposition: Mapped[str] = mapped_column(String(16))
    #: What the decision points at — "quote q7", "no ADR on record". Provenance,
    #: never a number.
    source_ref: Mapped[str] = mapped_column(String(255), default="")
    decided_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))

    #: When the outcome happened. NO_RESPONSE in particular is decided by a
    #: deadline passing, which is not when somebody got around to recording it.
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    #: When this platform wrote it down.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_now)
    #: Set when a later decision replaced this one. The row stays, and every
    #: current-state read filters ``IS NULL``.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))


class ApiKey(Base):
    """One machine credential for one organization's use of the public API.

    An ERP or CPQ partner calling ``POST /api/v1/resolve`` holds one of these
    and nothing else — no user, no session, no cookie. The row is what makes
    that credential answerable: it names the tenant the call acts for, the role
    the answer is projected to, and the moment it stopped being valid.

    **The role column is the point, not a convenience.** A key is a recipient
    like any other (CLAUDE.md §1), so the cost/margin withholding that decides
    what a salesperson's screen contains has to decide what this caller's JSON
    contains, by the same code path. Storing the role here is what lets
    ``authz.Principal`` be the *same* dataclass for both, and therefore
    ``commercial.quote_service.project`` the same function. A key without a
    role would have had to be given one at the call site, which is where the
    two would eventually disagree.

    **Only a hash is stored, and the secret is shown once.** The same rule as
    ``users.password_hash`` and for the same reason: a credential a support
    engineer can read out of a table is a credential the organization cannot
    say only they hold. ``key_id`` is the non-secret half — it travels in the
    presented key as a lookup handle, appears in logs, and identifies the row
    on the revoke endpoint.

    ``revoked_at`` is set, never deleted. A key that was used to resolve a
    line six months ago must still be nameable when somebody asks who did.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        # The management screen's read: this tenant's keys, live ones first.
        # `revoked_at` is out of the index for the reason `user_sessions` gives
        # — it is null for exactly the rows the query wants.
        Index("ix_api_keys_org", "organization_id", "created_at"),
    )

    #: The non-secret half of the presented credential, and the lookup handle.
    #: Random rather than derived: it is quoted in error messages and logs, so
    #: it must carry nothing about the organization holding it.
    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)

    #: What a person recognises this key by on the management screen — "Acme
    #: CPQ sandbox". Not a secret and not an identifier; two keys may share it.
    name: Mapped[str] = mapped_column(String(120), default="")

    #: PBKDF2 over the secret half, in ``app/passwords.py``'s format. Verified
    #: with the same constant-time comparison a password is.
    secret_hash: Mapped[str] = mapped_column(String(256))
    #: The last four characters of the secret, so a person can tell two keys
    #: apart in a list without either being reconstructable from four
    #: characters of a 43-character random string.
    secret_hint: Mapped[str] = mapped_column(String(8), default="")

    #: A ``Role``. The recipient this key's responses are projected to — see
    #: the class docstring. Defaults to the narrowest role rather than the
    #: creator's, because a key inheriting whoever happened to mint it is how an
    #: integration ends up reading cost it never needed.
    role: Mapped[str] = mapped_column(String(32), default="SALESPERSON")

    #: Requests per minute this key may make. Per key rather than per tenant so
    #: one partner's runaway loop cannot spend another integration's allowance,
    #: and zero means unlimited — see ``app/ratelimit.too_many``.
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: Advanced lazily, like ``user_sessions.last_seen_at`` and for the same
    #: reason: writing it on every call would put a write in front of every
    #: read. Null means "never used", which is the answer a key that was minted
    #: and forgotten should give.
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True))


# ── the decoded catalogue, per connected company ─────────────────────────────
#
# The catalogue used to be one file for the whole deployment. It is per
# connected company now: each company decodes its own item-master export
# through its own org-layer pack, and a quote resolves against the catalogue of
# the company it is raised from. `docs/per-company-catalogues.md` is the design.


class CompanyCorpus(Base):
    """One file a company's catalogue is decoded from, kept as bytes.

    A company has **several** of these at once, and that is the grain the
    ``source_key`` column carries: an item master exported from the ERP, a
    manufacturer's range extension, a price list covering products the master
    has not caught up with. They are merged into one corpus at build time
    (``catalog.combined_corpus``), which is where the record-id collisions
    between them are resolved and counted — pie-parser's ``AuthoritativeIndex``
    treats a duplicate identifier inside one namespace as a collision that
    never resolves, so merging without de-duplicating would silently stop a
    part number resolving at all.

    **Why the content is a column and not a path.** The container filesystem is
    ephemeral — ``railway.json`` declares no volume — and today that costs
    nothing, because ``PIE_CORPUS`` ships inside the image and the catalogue is
    derived from it in under two seconds. An *uploaded* corpus has no such
    source: written to container disk it is gone on the next deploy, and the
    company's catalogue could then never be rebuilt. Stored here it survives a
    redeploy, it is inside the ``pg_dump`` restore drill the gate already runs,
    and row-level security scopes it to its tenant with no new code.

    That keeps the property the deployment-wide design had by accident: the
    built JSONL is a *cache*, and losing the disk costs a rebuild rather than
    the data.

    **Append-only, superseded rather than mutated**, borrowing ``state/``'s
    convention. A catalogue row points at the corpus it was built from, so
    overwriting these bytes in place would silently change what an existing
    stamp refers to — the provenance would still be there, and it would be
    describing something else.
    """

    __tablename__ = "company_corpora"

    corpus_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The connected company this corpus describes. The grain of the whole
    #: feature: one organization reading three Zoho books has three item
    #: masters, and they are not interchangeable.
    connection_id: Mapped[str] = mapped_column(String(64), index=True)

    #: Which of this company's sources this file *is*. A re-upload under the
    #: same key supersedes that one file and leaves the others alone; a new key
    #: adds a source. Nullable because every row written before a company could
    #: have more than one predates the idea — those read as the unnamed source
    #: and behave exactly as they did.
    source_key: Mapped[Optional[str]] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    #: Which of this file's columns hold the record id, the description and the
    #: grade. An organisation fact about *this file*, not about the pack: two
    #: exports of the same catalogue call the part number ``MM#`` and
    #: ``Part No``, and asking a person to rename spreadsheet columns to match
    #: a pack is asking them to do the platform's job. Null means "not stated" —
    #: `ingestion.item_master.suggest_mapping` reads the headers instead.
    mapping: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    #: What reading this file kept and what it left out: the columns mapped, the
    #: columns dropped, and which of those were commercial. Evidence for the
    #: nomenclature-only rule that a person can check against their own file,
    #: rather than a count they have to trust.
    ingest: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    #: sha256 of ``content``. Also what makes a re-upload of identical bytes
    #: recognisable as such rather than a second corpus that happens to match.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    #: The file itself. **Deferred**: it is loaded only when something asks for
    #: it, because a company keeps several and the listing endpoint asks for
    #: none of them. Eager, a company with twenty 30 MB exports made
    #: ``GET /catalog/companies`` read 600 MB of blobs to render a file list.
    #: A build reads them one at a time and expires each after use.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)

    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Set when a newer corpus replaces this one. The row stays: a catalogue
    #: built from it keeps a real referent.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_company_corpora_company", "organization_id", "connection_id"),
    )


class CompanyCatalogue(Base):
    """What was built for one company, and the provenance it was stamped with.

    Replaces the ``products.run_report.json`` sidecar the deployment-wide
    version wrote beside the JSONL. A row rather than a file for the same
    reason the corpus is a row: the file does not survive a deploy, and the run
    report is the evidence behind every number the screen shows.

    The decoded JSONL itself stays on disk, under
    ``data/catalogues/<connection_id>/``. It is large, derived, and rebuildable
    from :class:`CompanyCorpus` at any time — which is exactly what makes it
    safe to lose.

    One row per company: a rebuild replaces it. There is no history of
    superseded catalogues here and there is not meant to be, on
    ``customer_item_metrics``' reasoning — it is derived state, and the corpus
    rows behind it are the append-only half.
    """

    __tablename__ = "company_catalogues"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: Which corpus this was decoded from, and which pack decoded it. Both are
    #: part of the answer to "which catalogue answered", alongside the stamp.
    #: ``corpus_id`` names the newest of the sources; with several of them it is
    #: no longer the whole answer, which is what ``corpus_digest`` is for.
    corpus_id: Mapped[Optional[str]] = mapped_column(String(64))
    pack: Mapped[str] = mapped_column(String(255), default="")

    #: A hash over the set of sources this was built from — every live source's
    #: key and content digest. What makes "out of date" answerable once a
    #: company has several files: a source added, replaced or removed all move
    #: this, and comparing one ``corpus_id`` could not see any of the three.
    #: Null on a row built before there was more than one source, where the
    #: ``corpus_id`` comparison is still the honest answer.
    corpus_digest: Mapped[Optional[str]] = mapped_column(String(64))
    #: The sources themselves, as they were at build time — filename, key,
    #: digest and rows contributed. Provenance: with several files merged, "what
    #: is in this catalogue" is not answerable from a single filename.
    sources: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    #: What merging them did: rows in, rows kept, and the record ids that
    #: appeared in more than one file. A collision is resolved in favour of the
    #: newest source and *counted* — never silently dropped, because the same
    #: part number in two price lists is a real disagreement about a real
    #: product and somebody has to know.
    ingest: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    records: Mapped[int] = mapped_column(Integer, default=0)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[Optional[float]] = mapped_column(Float)

    #: pie-parser's own ``RunReport.to_dict()``, stored whole and served
    #: verbatim. The portal never recomputes a census or a parse rate — a
    #: second parse-rate calculation is the semantic duplication CLAUDE.md §2
    #: is about, and the parser has already answered.
    report: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    #: The stamp every emitted record carries. ``run_id`` derives from the
    #: input bytes plus ``ruleset_checksum``, so these are what say *which*
    #: catalogue answered a given resolution.
    pack_id: Mapped[Optional[str]] = mapped_column(String(128))
    pack_version: Mapped[Optional[str]] = mapped_column(String(64))
    org_id: Mapped[Optional[str]] = mapped_column(String(128))
    org_version: Mapped[Optional[str]] = mapped_column(String(64))
    ruleset_checksum: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64))
    engine_version: Mapped[Optional[str]] = mapped_column(String(32))
    schema_version: Mapped[Optional[str]] = mapped_column(String(32))

    built_by: Mapped[Optional[str]] = mapped_column(String(64))
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
