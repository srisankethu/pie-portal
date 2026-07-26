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
        row.source_ref = r.source_ref.model_dump()
        return row

    def count(self, model) -> int:
        return len(self.s.scalars(
            select(model).where(model.organization_id == self.org)).all())


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
        elif action is HumanAction.SNOOZE:
            pass  # snooze keeps status; scheduling deferred to the outcome phase
        return decision


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
