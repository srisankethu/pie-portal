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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
    erp: Mapped[str] = mapped_column(String(32), default="zoho")
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    # The zone the business's *day* is measured in. Storage stays UTC; this is
    # what decides which day a timestamp falls on, and it belongs to the tenant
    # for the same reason the currency does — one instance can hold an Indian
    # distributor and a Gulf one. Zoho reports it on the organization record, so
    # a connected company fills it in rather than being asked.
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
    """

    __tablename__ = "zoho_credentials"

    credential_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # Who may rotate it and decide who else may use it.
    owner_organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(255), default="")

    client_id: Mapped[str] = mapped_column(String(255))
    client_secret_encrypted: Mapped[str] = mapped_column(String(2048))
    refresh_token_encrypted: Mapped[str] = mapped_column(String(2048))
    accounts_base: Mapped[str] = mapped_column(String(255),
                                               default="https://accounts.zoho.in")
    api_base: Mapped[str] = mapped_column(String(255),
                                          default="https://www.zohoapis.in/books/v3")

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
    """

    __tablename__ = "zoho_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "zoho_organization_id",
                         name="uq_zoho_connection_org_company"),
    )

    connection_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                               default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    # What a person calls this company. The Zoho org id is the identity; this is
    # what makes a list of three connections readable.
    label: Mapped[str] = mapped_column(String(255), default="")
    # Off means "keep the credentials, skip it on a sync-all". Deleting is for
    # connections that are wrong; disabling is for ones that are simply quiet.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    zoho_organization_id: Mapped[str] = mapped_column(String(64))
    credential_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("zoho_credentials.credential_id"), index=True)

    # Legacy inline credentials. Rows created before credentials were separated
    # keep working from these until the migration backfills them; nothing new is
    # ever written here.
    client_id: Mapped[Optional[str]] = mapped_column(String(255))
    client_secret_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(String(2048))
    accounts_base: Mapped[str] = mapped_column(String(255), default="https://accounts.zoho.in")
    api_base: Mapped[str] = mapped_column(String(255),
                                          default="https://www.zohoapis.in/books/v3")
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


class CommercialPolicy(Base):
    """One organization's overrides to the commercial thresholds.

    Only the fields ``commercial/policy.py`` marks editable are ever stored, and
    the whole set is validated as a ladder before it is written. Kept as JSON
    rather than columns because the editable set is a product decision that will
    move, and a migration per threshold is a tax on changing one's mind.

    Reproducibility survives editing because ``CommercialThresholds.version`` is
    a content hash: an edited policy has a different version, and every metric
    row, signal and quote snapshot already records the version that produced it.
    """

    __tablename__ = "commercial_policies"

    organization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), index=True)
    # email is an auth affordance beyond the spec's minimal User; nullable/unique.
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── credentials ──────────────────────────────────────────────────────────
    # Nullable, and a null means "cannot sign in" — never "any password works",
    # which is what the login endpoint previously did and which made the three
    # roles a display preference rather than a boundary.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Who created this account, and who last changed its role. Role changes are
    # the most security-relevant edit in the product; an unattributed one is not
    # worth recording.
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    role_changed_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    role_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_salestxn_org_ref"),
        Index("ix_sales_txns_org_customer_product",
              "organization_id", "customer_id", "product_id"),
        Index("ix_sales_txns_org_product_date",
              "organization_id", "product_id", "date"),
    )

    sales_txn_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_costrecord_org_ref"),
        # Declared for the same reason as the sales_txns pair above: it is what
        # makes the effective-cost lookup for a customer-item pair cheap.
        Index("ix_cost_records_org_product_date",
              "organization_id", "product_id", "date"),
    )

    cost_record_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    external_ref: Mapped[str] = mapped_column(String(160), index=True)  # bill_id:line_id
    product_id: Mapped[str] = mapped_column(String(64), index=True)
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
    """Transient input for QUOTE_CONTEXT (§4). Lines stored as JSON."""

    __tablename__ = "quote_drafts"

    quote_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    salesperson_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ── Signal (immutable deterministic fact, §6) ────────────────────────────────
class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_type: Mapped[str] = mapped_column(String(48), index=True)
    subject_entity_type: Mapped[str] = mapped_column(String(32))
    subject_entity_id: Mapped[str] = mapped_column(String(64), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    detector_version: Mapped[str] = mapped_column(String(32))
    threshold_config_version: Mapped[str] = mapped_column(String(32))
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
    subject_entity_id: Mapped[str] = mapped_column(String(64), index=True)
    assigned_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    assigned_role: Mapped[str] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
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
    subject_entity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
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


class IngestedDocument(Base):
    """One Zoho document already pulled — the resume cursor.

    Zoho's list endpoints omit line items, so every invoice and bill costs its
    own detail call. Recording what has been fetched (and the modification stamp
    it was fetched at) means an interrupted pull resumes for the price of the
    list calls alone, and a document edited in Zoho is re-fetched because its
    stamp moved.
    """

    __tablename__ = "ingested_documents"
    __table_args__ = (UniqueConstraint("organization_id", "doc_type", "doc_id",
                                       name="uq_ingested_org_type_doc"),)

    ingested_document_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)   # invoice | bill
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    modified_at: Mapped[Optional[str]] = mapped_column(String(64))  # Zoho's stamp, verbatim
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
    thresholds_version: Mapped[str] = mapped_column(String(32), default="")
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

    thresholds_version: Mapped[str] = mapped_column(String(32), default="")


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
    thresholds_version: Mapped[str] = mapped_column(String(32), default="")
    engine_version: Mapped[str] = mapped_column(String(32), default="")
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
    """

    __tablename__ = "quote_outcomes"
    __table_args__ = (
        UniqueConstraint("organization_id", "quote_id", name="uq_quote_outcome_org_quote"),
    )

    quote_outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                  default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    quote_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_ref: Mapped[str] = mapped_column(String(255), default="")
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    note: Mapped[Optional[str]] = mapped_column(String(1024))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


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
    """Proof of what was destroyed, signed, and readable after the fact.

    Stored unencrypted on purpose: a receipt sealed under the key whose
    destruction it certifies would be unreadable exactly when it is wanted.
    """

    __tablename__ = "erasure_receipts"

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
# The Tier 3 views compute from them at request time, the way every other
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_payment_org_external"),
        Index("ix_payment_org_date", "organization_id", "date"),
    )

    payment_receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                    default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_payment_application_org_external"),
        Index("ix_payment_app_org_invoice", "organization_id", "invoice_external_ref"),
    )

    payment_application_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                        default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_sales_order_org_ref"),
    )

    sales_order_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_bill_org_ref"),
        Index("ix_bill_org_due", "organization_id", "due_date"),
    )

    bill_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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


class VendorPaymentDoc(Base):
    """Money out, at the payment grain.

    ``PaymentReceipt`` has recorded money in since the cash screen was built.
    Receipts alone are not cash — they are revenue collected — so liquidity and
    working capital could not be computed from one side of the ledger. This is
    the other side.

    No application table beside it, deliberately: Zoho's vendor payment carries
    which bills it settled, but nothing in the platform computes supplier
    ageing yet, and a table nobody reads is a table that silently rots. It is
    added when the first reader exists.
    """

    __tablename__ = "vendor_payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_vendor_payment_org_ref"),
    )

    vendor_payment_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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


class PurchaseOrderDoc(Base):
    """An order placed on a supplier, and how much of it has arrived.

    Header grain only. The line detail matters for a receiving screen and this
    is not one — the questions here are "what is still outstanding, with whom,
    and for how long", all of which the header answers.
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_ref",
                         name="uq_purchase_order_org_external"),
        Index("ix_po_org_date", "organization_id", "date"),
    )

    purchase_order_id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                                   default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
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
