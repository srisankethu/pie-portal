"""SQLAlchemy ORM models.

Structure mirrors the approved spec §4–§8. Design choices:

- ``organization_id`` on every entity (§4) — carried for future multi-org, but
  no cross-org logic exists; all access is scoped to one org.
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
    ForeignKey,
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
    unit_price: Mapped[Any] = mapped_column(Numeric(18, 4))
    line_revenue: Mapped[Any] = mapped_column(Numeric(18, 4))
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
    unit_cost: Mapped[Any] = mapped_column(Numeric(18, 4))
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
