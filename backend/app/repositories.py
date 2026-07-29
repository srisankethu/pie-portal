"""Organization-scoped repositories.

Every query is scoped to a single ``organization_id``. This is the enforcement
seam for organization isolation: callers pass their org, and no repository
method can read or write across orgs. There is deliberately **no** cross-org
query surface (V1 is single-org; multi-org is out of scope).

Upserts key on the natural ``(organization_id, external_id/ref)`` so re-running a
sync is idempotent and never duplicates a source record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import models
from .domain.enums import DecisionStatus, HumanAction
from .domain.schemas import CostRecordIn, CustomerIn, ProductIn, SalesTxnIn


class ReadModelRepository:
    """Upsert + lookup for the canonical read model, scoped to one org."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    # ── customers ────────────────────────────────────────────────────────────
    def upsert_customer(self, c: CustomerIn) -> models.Customer:
        row = self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == c.external_id,
            )
        )
        if row is None:
            row = models.Customer(organization_id=self.org, external_id=c.external_id)
            self.s.add(row)
        row.name = c.name
        row.status = c.status.value
        row.first_seen = c.first_seen
        if c.assigned_user_id is not None:
            row.assigned_user_id = c.assigned_user_id
        row.source_ref = c.source_ref.model_dump()
        return row

    def get_customer_by_external(self, external_id: str) -> Optional[models.Customer]:
        return self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == external_id,
            )
        )

    def list_customers(self) -> Sequence[models.Customer]:
        return self.s.scalars(
            select(models.Customer).where(models.Customer.organization_id == self.org)
        ).all()

    # ── products ─────────────────────────────────────────────────────────────
    def upsert_product(self, p: ProductIn) -> models.Product:
        row = self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == p.external_id,
            )
        )
        if row is None:
            row = models.Product(organization_id=self.org, external_id=p.external_id)
            self.s.add(row)
        row.name = p.name
        row.uom = p.uom
        row.hsn = p.hsn
        row.active = p.active
        row.source_ref = p.source_ref.model_dump()
        return row

    def get_product_by_external(self, external_id: str) -> Optional[models.Product]:
        return self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == external_id,
            )
        )

    # ── sales / cost ─────────────────────────────────────────────────────────
    def upsert_sales_txn(self, t: SalesTxnIn, customer_id: str, product_id: str) -> models.SalesTxn:
        row = self.s.scalar(
            select(models.SalesTxn).where(
                models.SalesTxn.organization_id == self.org,
                models.SalesTxn.external_ref == t.external_ref,
            )
        )
        if row is None:
            row = models.SalesTxn(organization_id=self.org, external_ref=t.external_ref)
            self.s.add(row)
        row.customer_id = customer_id
        row.product_id = product_id
        row.date = t.date
        row.qty = t.qty
        row.unit_price = t.unit_price
        row.line_revenue = t.line_revenue
        row.rate = t.rate
        row.discount_percent = t.discount_percent
        row.source_ref = t.source_ref.model_dump()
        return row

    def upsert_cost_record(self, r: CostRecordIn, product_id: str) -> models.CostRecord:
        row = self.s.scalar(
            select(models.CostRecord).where(
                models.CostRecord.organization_id == self.org,
                models.CostRecord.external_ref == r.external_ref,
            )
        )
        if row is None:
            row = models.CostRecord(organization_id=self.org, external_ref=r.external_ref)
            self.s.add(row)
        row.product_id = product_id
        row.date = r.date
        row.qty = r.qty
        row.unit_cost = r.unit_cost
        row.rate = r.rate
        row.discount_percent = r.discount_percent
        row.source_ref = r.source_ref.model_dump()
        return row

    def count_cost_records_pending_discount_backfill(self) -> int:
        """Rows synced before the discount-aware fix — ``rate`` is only ever
        null on a legacy row, since every current write sets it. Re-syncing
        with ``full=True`` re-fetches the bill and corrects it."""
        return len(self.s.scalars(
            select(models.CostRecord).where(
                models.CostRecord.organization_id == self.org,
                models.CostRecord.rate.is_(None),
            )).all())

    def count(self, model) -> int:
        return len(self.s.scalars(
            select(model).where(model.organization_id == self.org)).all())

    # ── users (for ownership mapping) ────────────────────────────────────────
    def users_by_email(self) -> dict[str, models.User]:
        return {
            u.email.strip().lower(): u
            for u in self.s.scalars(
                select(models.User).where(models.User.organization_id == self.org))
            if u.email
        }

    # ── resume cursor ────────────────────────────────────────────────────────
    def ingested_index(self, doc_type: str) -> dict[str, str]:
        """``{doc_id: modified_at}`` for documents already pulled."""
        return {
            r.doc_id: (r.modified_at or "")
            for r in self.s.scalars(
                select(models.IngestedDocument).where(
                    models.IngestedDocument.organization_id == self.org,
                    models.IngestedDocument.doc_type == doc_type,
                ))
        }

    def mark_ingested(self, doc_type: str, doc_id: str, modified_at: str) -> None:
        row = self.s.scalar(
            select(models.IngestedDocument).where(
                models.IngestedDocument.organization_id == self.org,
                models.IngestedDocument.doc_type == doc_type,
                models.IngestedDocument.doc_id == doc_id,
            )
        )
        if row is None:
            row = models.IngestedDocument(organization_id=self.org, doc_type=doc_type,
                                          doc_id=doc_id)
            self.s.add(row)
        row.modified_at = modified_at or None
        row.fetched_at = datetime.now(timezone.utc)

    def clear_ingested(self) -> int:
        """Forget the cursor, so the next pull re-fetches every document."""
        rows = self.s.scalars(
            select(models.IngestedDocument).where(
                models.IngestedDocument.organization_id == self.org)).all()
        for r in rows:
            self.s.delete(r)
        return len(rows)


class DecisionRepository:
    """Persistence + lifecycle for decisions, scoped to one org."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def get(self, decision_id: str) -> Optional[models.Decision]:
        return self.s.scalar(
            select(models.Decision).where(
                models.Decision.organization_id == self.org,
                models.Decision.decision_id == decision_id,
            )
        )

    def get_by_key(self, decision_key: str) -> Optional[models.Decision]:
        return self.s.scalar(
            select(models.Decision).where(
                models.Decision.organization_id == self.org,
                models.Decision.decision_key == decision_key,
            )
        )

    def add(self, decision: models.Decision) -> models.Decision:
        assert decision.organization_id == self.org, "cross-org write blocked"
        self.s.add(decision)
        return decision

    def list(
        self,
        *,
        assigned_user_id: Optional[str] = None,
        exclude_types: Sequence[str] = (),
        decision_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Sequence[models.Decision]:
        """Scoped list. ``assigned_user_id`` restricts to a salesperson's decisions;
        ``exclude_types`` removes restricted types from a salesperson's view."""
        stmt = select(models.Decision).where(models.Decision.organization_id == self.org)
        if assigned_user_id is not None:
            stmt = stmt.where(models.Decision.assigned_user_id == assigned_user_id)
        if exclude_types:
            stmt = stmt.where(models.Decision.decision_type.notin_(list(exclude_types)))
        if decision_type is not None:
            stmt = stmt.where(models.Decision.decision_type == decision_type)
        if status is not None:
            stmt = stmt.where(models.Decision.status == status)
        stmt = stmt.order_by(models.Decision.priority_score.desc(),
                             models.Decision.detected_at.desc())
        return self.s.scalars(stmt).all()

    def record_human_action(
        self, decision: models.Decision, action: HumanAction, actor_user_id: str,
        note: Optional[str] = None, reason: Optional[str] = None,
    ) -> models.Decision:
        """Apply a human action and advance the lifecycle (human-driven transitions only).

        Detector-driven transitions (RESOLVED / SUPERSEDED / EXPIRED) are out of
        Phase 1 scope and are not performed here.
        """
        now = datetime.now(timezone.utc)
        decision.human_action = {
            "action": action.value, "actor_user_id": actor_user_id,
            "acted_at": now.isoformat(), "note": note,
        }
        if action is HumanAction.VIEW:
            if decision.status == DecisionStatus.OPEN.value:
                decision.status = DecisionStatus.VIEWED.value
        elif action is HumanAction.ACT:
            decision.status = DecisionStatus.ACTIONED.value
        elif action is HumanAction.DISMISS:
            decision.status = DecisionStatus.DISMISSED.value
            decision.override_reason = reason
        elif action is HumanAction.OVERRIDE:
            decision.status = DecisionStatus.OVERRIDDEN.value
            decision.override_reason = reason
        elif action is HumanAction.ESCALATE:
            # Parked, not closed. The approval request raised alongside this is
            # what actually routes it; settling that request moves the decision
            # on (see approvals._settle_escalated_decision).
            decision.status = DecisionStatus.ESCALATED.value
        elif action is HumanAction.SNOOZE:
            pass  # snooze keeps status; scheduling deferred to the outcome phase
        elif action is HumanAction.REOPEN:
            # Undo: return the decision to the queue and clear the reason that
            # closed it. The REOPEN itself stays in human_action, so the audit
            # trail records that a human reversed the earlier call.
            decision.status = DecisionStatus.OPEN.value
            decision.override_reason = None
        return decision


class AiTelemetryRepository:
    """AI call telemetry, scoped to one org (WS3).

    Writing is best-effort by design: observability must never be able to fail a
    decision. Reads power the owner-scoped ops metrics endpoint.
    """

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def record(self, tel, *, cache_hit: bool = False) -> Optional[models.AiCallLog]:
        """Persist one telemetry record. Returns None when disabled."""
        from .config import settings

        if not settings.AI_TELEMETRY_ENABLED or tel is None:
            return None
        row = models.AiCallLog(
            organization_id=self.org,
            decision_type=tel.decision_type or "",
            subject_entity_id=tel.subject_entity_id,
            recipient_role=tel.recipient_role,
            provider=tel.provider or "", model=tel.model or "",
            prompt_version=tel.prompt_version or "",
            context_hash=tel.context_hash or "",
            ai_status=tel.ai_status or "",
            provider_called=bool(tel.provider_called),
            cache_hit=bool(cache_hit or tel.cache_hit),
            attempts=int(tel.attempts or 0),
            latency_ms=tel.latency_ms,
            input_tokens=tel.input_tokens, output_tokens=tel.output_tokens,
            estimated_cost_usd=tel.estimated_cost_usd,
            failure_reason=tel.failure_reason,
            corrections=list(tel.corrections or []),
        )
        self.s.add(row)
        return row

    def since(self, cutoff: datetime) -> Sequence[models.AiCallLog]:
        return self.s.scalars(
            select(models.AiCallLog).where(
                models.AiCallLog.organization_id == self.org,
                models.AiCallLog.created_at >= cutoff,
            )
        ).all()


class SignalRepository:
    """Write-once signal persistence, scoped to one org."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def add(self, signal: models.Signal) -> models.Signal:
        assert signal.organization_id == self.org, "cross-org write blocked"
        self.s.add(signal)
        return signal

    def get(self, signal_id: str) -> Optional[models.Signal]:
        return self.s.scalar(
            select(models.Signal).where(
                models.Signal.organization_id == self.org,
                models.Signal.signal_id == signal_id,
            )
        )
