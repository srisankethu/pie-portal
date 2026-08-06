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
from .domain.schemas import (BillIn, CostRecordIn, CustomerIn, PaymentReceiptIn, ProductIn,
                            PurchaseOrderIn, SalesOrderIn, SalesTxnIn,
                            StockSnapshotIn, VendorIn, VendorPaymentIn)


class ReadModelRepository:
    """Upsert + lookup for the canonical read model, scoped to one org."""

    def __init__(self, session: Session, organization_id: str, *,
                 connector: Optional[str] = None,
                 connection_id: Optional[str] = None) -> None:
        self.s = session
        self.org = organization_id
        # Which connected company this repository is writing on behalf of.
        # An imported record's identity is (connector, connection, that
        # system's id) — an external id alone is unique only inside the system
        # that issued it, and two connected companies are two systems.
        #
        # Optional so every existing caller that only *reads* keeps working.
        # A writer that has not said where its rows come from writes NULLs,
        # which read back as "source not recorded" rather than as a guess.
        self.connector = connector
        self.connection_id = connection_id

    def _source(self, model) -> list:
        """The clauses that pin a lookup to this repository's own source.

        Written once because it is easy to get subtly wrong per call site: a
        NULL ``connection_id`` has to match a NULL, and ``== None`` does that
        in SQLAlchemy while a Python ``is None`` comparison silently does not.
        """
        return [model.connector == self.connector,
                model.connection_id == self.connection_id]

    # ── customers ────────────────────────────────────────────────────────────
    def upsert_customer(self, c: CustomerIn) -> models.Customer:
        row = self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == c.external_id,
                *self._source(models.Customer),
            )
        )
        if row is None:
            row = models.Customer(organization_id=self.org, external_id=c.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = c.name
        row.status = c.status.value
        row.first_seen = c.first_seen
        if c.assigned_user_id is not None:
            row.assigned_user_id = c.assigned_user_id
        row.source_ref = c.source_ref.model_dump()
        return row

    def get_customer_by_external(self, external_id: str) -> Optional[models.Customer]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's customers and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == external_id,
                *self._source(models.Customer),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == external_id,
                models.Customer.connection_id.is_(None),
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
                *self._source(models.Product),
            )
        )
        if row is None:
            row = models.Product(organization_id=self.org, external_id=p.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = p.name
        row.uom = p.uom
        row.hsn = p.hsn
        row.active = p.active
        row.source_ref = p.source_ref.model_dump()
        return row

    def get_product_by_external(self, external_id: str) -> Optional[models.Product]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's products and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == external_id,
                *self._source(models.Product),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == external_id,
                models.Product.connection_id.is_(None),
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


    # ── supply, stock and cash ───────────────────────────────────────────────
    #
    # Same upsert-by-(org, external ref) shape as everything above. Keyed on
    # Zoho's own ids so a re-pull of the same window updates rather than
    # duplicating — which is what makes a resumed or overlapping sync safe.

    def upsert_vendor(self, v: VendorIn) -> models.Vendor:
        row = self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == v.external_id,
                *self._source(models.Vendor),
            )
        )
        if row is None:
            row = models.Vendor(organization_id=self.org, external_id=v.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = v.name
        row.gstin = v.gstin
        row.pan = v.pan
        row.payment_terms_days = v.payment_terms_days
        row.status = v.status.value
        row.source_ref = v.source_ref.model_dump()
        return row

    def get_vendor_by_external(self, external_id: str) -> Optional[models.Vendor]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's vendors and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == external_id,
                *self._source(models.Vendor),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == external_id,
                models.Vendor.connection_id.is_(None),
            )
        )

    def upsert_stock_snapshot(self, product_id: str,
                              snap: StockSnapshotIn) -> models.StockSnapshot:
        """One row per item per day.

        Upserted rather than appended: a sync run twice in one afternoon is a
        correction, not two observations, and two rows for one day would make
        every later average silently weight that day double.
        """
        row = self.s.scalar(
            select(models.StockSnapshot).where(
                models.StockSnapshot.organization_id == self.org,
                models.StockSnapshot.product_id == product_id,
                models.StockSnapshot.as_of == snap.as_of,
            )
        )
        if row is None:
            row = models.StockSnapshot(organization_id=self.org, product_id=product_id,
                                       as_of=snap.as_of)
            self.s.add(row)
        row.on_hand = snap.on_hand
        row.available = snap.available
        row.actual_available = snap.actual_available
        row.reorder_level = snap.reorder_level
        row.purchase_rate = snap.purchase_rate
        row.tracked = snap.tracked
        row.source_ref = snap.source_ref.model_dump()
        return row

    def upsert_payment(self, customer_id: str,
                       p: PaymentReceiptIn) -> models.PaymentReceipt:
        row = self.s.scalar(
            select(models.PaymentReceipt).where(
                models.PaymentReceipt.organization_id == self.org,
                models.PaymentReceipt.external_ref == p.external_ref,
            )
        )
        if row is None:
            row = models.PaymentReceipt(organization_id=self.org,
                                        external_ref=p.external_ref)
            self.s.add(row)
        row.customer_id = customer_id
        row.date = p.date
        row.amount = p.amount
        row.mode = p.mode
        row.is_advance = p.is_advance
        row.unapplied_amount = p.unapplied_amount
        row.source_ref = p.source_ref.model_dump()
        self.s.flush()

        # Applications are replaced wholesale rather than merged: a payment
        # re-applied in Zoho can drop an invoice, and a merge would leave the
        # old application behind as a settlement that no longer exists.
        existing = {
            a.external_ref: a
            for a in self.s.scalars(
                select(models.PaymentApplication).where(
                    models.PaymentApplication.organization_id == self.org,
                    models.PaymentApplication.payment_receipt_id == row.payment_receipt_id,
                )).all()
        }
        seen: set[str] = set()
        for a in p.applications:
            seen.add(a.external_ref)
            app_row = existing.get(a.external_ref)
            if app_row is None:
                app_row = models.PaymentApplication(
                    organization_id=self.org, external_ref=a.external_ref,
                    payment_receipt_id=row.payment_receipt_id)
                self.s.add(app_row)
            app_row.customer_id = customer_id
            app_row.invoice_external_ref = a.invoice_external_ref
            app_row.invoice_number = a.invoice_number
            app_row.invoice_date = a.invoice_date
            app_row.invoice_due_date = a.invoice_due_date
            app_row.paid_on = p.date
            app_row.amount_applied = a.amount_applied
            app_row.source_ref = p.source_ref.model_dump()
        for ref, stale in existing.items():
            if ref not in seen:
                self.s.delete(stale)
        return row

    def upsert_sales_order(self, customer_id: Optional[str],
                           so: SalesOrderIn) -> models.SalesOrderDoc:
        """One customer order, keyed on the id its ERP gave it.

        Keyed on ``external_ref`` alone rather than on the source triple, like
        every other *document* here — documents already carry a globally unique
        id from their own system and are scoped by the connection that fetched
        them. The source triple matters for masters (customers, items, vendors),
        where two connected companies genuinely number from one.
        """
        row = self.s.scalar(
            select(models.SalesOrderDoc).where(
                models.SalesOrderDoc.organization_id == self.org,
                models.SalesOrderDoc.external_ref == so.external_ref,
            )
        )
        if row is None:
            row = models.SalesOrderDoc(organization_id=self.org,
                                       external_ref=so.external_ref)
            self.s.add(row)
        row.number = so.number
        row.customer_id = customer_id
        row.date = so.date
        row.expected_ship_date = so.expected_ship_date
        row.status = so.status
        row.invoiced_status = so.invoiced_status
        row.shipped_status = so.shipped_status
        row.total = so.total
        row.salesperson_external_id = so.salesperson_external_id
        row.source_ref = so.source_ref.model_dump()
        return row

    def upsert_bill(self, vendor_id: Optional[str], b: BillIn) -> models.BillDoc:
        """The payable header. Re-read on every pull that touches the bill,
        because ``status`` and ``balance`` change as it is paid — a bill row
        written once and never revisited would report every settled bill as
        still outstanding."""
        row = self.s.scalar(
            select(models.BillDoc).where(
                models.BillDoc.organization_id == self.org,
                models.BillDoc.external_ref == b.external_ref,
            )
        )
        if row is None:
            row = models.BillDoc(organization_id=self.org,
                                 external_ref=b.external_ref)
            self.s.add(row)
        row.number = b.number
        row.vendor_id = vendor_id
        row.date = b.date
        row.due_date = b.due_date
        row.status = b.status
        row.total = b.total
        row.balance = b.balance
        row.source_ref = b.source_ref.model_dump()
        return row

    def upsert_vendor_payment(self, vendor_id: Optional[str],
                              vp: VendorPaymentIn) -> models.VendorPaymentDoc:
        row = self.s.scalar(
            select(models.VendorPaymentDoc).where(
                models.VendorPaymentDoc.organization_id == self.org,
                models.VendorPaymentDoc.external_ref == vp.external_ref,
            )
        )
        if row is None:
            row = models.VendorPaymentDoc(organization_id=self.org,
                                          external_ref=vp.external_ref)
            self.s.add(row)
        row.vendor_id = vendor_id
        row.date = vp.date
        row.amount = vp.amount
        row.mode = vp.mode
        row.reference = vp.reference
        row.source_ref = vp.source_ref.model_dump()
        return row

    def upsert_purchase_order(self, vendor_id: Optional[str],
                              po: PurchaseOrderIn) -> models.PurchaseOrderDoc:
        row = self.s.scalar(
            select(models.PurchaseOrderDoc).where(
                models.PurchaseOrderDoc.organization_id == self.org,
                models.PurchaseOrderDoc.external_ref == po.external_ref,
            )
        )
        if row is None:
            row = models.PurchaseOrderDoc(organization_id=self.org,
                                          external_ref=po.external_ref)
            self.s.add(row)
        row.number = po.number
        row.vendor_id = vendor_id
        row.date = po.date
        row.expected_date = po.expected_date
        row.status = po.status
        row.received_status = po.received_status
        row.ordered_qty = po.ordered_qty
        row.pending_qty = po.pending_qty
        row.total = po.total
        row.received_on = po.received_on
        row.source_ref = po.source_ref.model_dump()
        return row


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
