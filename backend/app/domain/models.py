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
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ZohoConnection(Base):
    """One organization's Zoho Books connection — one platform tenant, one
    Zoho Books company. Each org is a fully separate tenant, so this is a
    one-to-one relationship, not a list: connecting a second Zoho company
    means provisioning a second organization (see ``app/provision_org.py``),
    not adding a second row here.

    ``client_secret`` and ``refresh_token`` are encrypted at rest (see
    ``app/crypto.py``) — this table, unlike a ``.env`` file, can end up in a
    database backup or a read replica. Pull tuning (pacing, retries, page
    size, history window) is deliberately NOT here: it is shared, global
    operational behaviour in ``config.py``, not part of an account's identity.

    The platform's original default organization has no row here until someone
    explicitly connects it — until then it falls back to the ``ZOHO_*``
    environment variables, so an existing single-tenant deployment keeps
    working unchanged (see ``ingestion/connections.py``).
    """

    __tablename__ = "zoho_connections"

    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.organization_id"), primary_key=True)
    zoho_organization_id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(255))
    client_secret_encrypted: Mapped[str] = mapped_column(String(2048))
    refresh_token_encrypted: Mapped[str] = mapped_column(String(2048))
    accounts_base: Mapped[str] = mapped_column(String(255), default="https://accounts.zoho.in")
    api_base: Mapped[str] = mapped_column(String(255),
                                          default="https://www.zohoapis.in/books/v3")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
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
    __table_args__ = (UniqueConstraint("organization_id", "external_id",
                                       name="uq_customer_org_external"),)

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)  # Zoho contact_id
    name: Mapped[str] = mapped_column(String(255))
    assigned_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # Zoho's salesperson on this account's most recent invoice, kept as Zoho's own
    # id. Held separately from assigned_user_id so ownership survives a resumed
    # pull that never re-reads those invoices, and so the mapping stays reversible.
    source_owner_id: Mapped[Optional[str]] = mapped_column(String(64))
    source_owner_at: Mapped[Optional[date]] = mapped_column(Date)
    first_seen: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "external_id",
                                       name="uq_product_org_external"),)

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)  # Zoho item_id
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
    __table_args__ = (UniqueConstraint("organization_id", "external_ref",
                                       name="uq_salestxn_org_ref"),)

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
    __table_args__ = (UniqueConstraint("organization_id", "external_ref",
                                       name="uq_costrecord_org_ref"),)

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
    """One ingestion run — what was pulled, what was skipped, and whether it worked.

    Persisted so the UI can answer "is Zoho connected, and when did data last
    arrive?" without re-hitting the API. A failed run is recorded too: silence
    about a failure is exactly what made the connection unreadable before.

    A run that dies part-way is ``PARTIAL``, not ``FAILED``: rows that did land
    are kept (they are what makes the next attempt cheap), and reporting zero
    for them would misdescribe the database.
    """

    __tablename__ = "sync_runs"

    sync_run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16))           # "api" | "fixture"
    status: Mapped[str] = mapped_column(String(16), index=True)  # OK | PARTIAL | FAILED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    customers: Mapped[int] = mapped_column(Integer, default=0)
    products: Mapped[int] = mapped_column(Integer, default=0)
    sales_txns: Mapped[int] = mapped_column(Integer, default=0)
    cost_records: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_sample: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
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
