"""Explicit, validated schemas.

Two roles:
- **Canonical ingestion DTOs** (``CustomerIn`` … ``CostRecordIn``) are what the
  normalizer emits from raw Zoho payloads. Validation happens here — malformed
  source rows fail loudly at this boundary and are reported, never silently
  written. Raw Zoho shapes never travel past normalization.
- **API read DTOs** (``DecisionRead``) are the scope-filtered projections the
  service layer returns.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import CustomerStatus


class SourceRef(BaseModel):
    """A pointer back to the originating ERP record (provenance)."""

    system: str = "zoho"
    record_type: str            # contact | item | invoice | bill
    record_id: str
    line_id: Optional[str] = None


class CustomerIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: CustomerStatus = CustomerStatus.ACTIVE
    first_seen: Optional[date] = None
    assigned_user_id: Optional[str] = None
    source_ref: SourceRef


class ProductIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    uom: Optional[str] = None
    hsn: Optional[str] = None
    #: The catalogue's own words, carried through unnormalised. See
    #: ``models.Product.category`` for why it is not mapped on the way in.
    category: Optional[str] = None
    active: bool = True
    source_ref: SourceRef


class SalesTxnIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)          # invoice_id:line_id
    customer_external_id: str = Field(min_length=1)
    product_external_id: str = Field(min_length=1)
    date: date
    qty: Decimal
    unit_price: Decimal       # NET of line discount — what the customer paid
    line_revenue: Decimal     # pre-tax, post-discount
    rate: Optional[Decimal] = None              # original list rate (audit)
    discount_percent: Optional[Decimal] = None  # audit only
    source_ref: SourceRef

    @field_validator("qty", "unit_price", "line_revenue", "rate", "discount_percent",
                     mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        # Parse via str so floats don't introduce binary-float noise (determinism).
        return v if isinstance(v, Decimal) else Decimal(str(v))


class CostRecordIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)          # bill_id:line_id
    product_external_id: str = Field(min_length=1)
    #: Who it was bought from, copied down from the bill header.
    #:
    #: A dimension, not a measure — unlike ``balance`` and ``due_date``, which
    #: live on ``BillIn`` precisely because copying them per line would turn a
    #: sum into a de-duplication problem. Spend by supplier needs the vendor at
    #: *this* grain, and without it ``INVENTORY.spend`` could only ever be per
    #: product, which is why supplier concentration was unanswerable.
    #:
    #: Optional because events written before this field existed do not carry
    #: it. Those lines fold as unattributable rather than being guessed at — the
    #: same degradation ``CostRecord.rate`` already documents, and the same fix:
    #: a re-sync from Zoho.
    vendor_external_id: Optional[str] = None
    date: date
    qty: Decimal
    unit_cost: Decimal        # effective, post-discount — what every cost consumer reads
    rate: Decimal             # the bill line's original list rate, pre-discount (audit)
    discount_percent: Optional[Decimal] = None   # audit only; None when not determinable
    source_ref: SourceRef

    @field_validator("qty", "unit_cost", "rate", "discount_percent", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


# ── API read DTOs ────────────────────────────────────────────────────────────
class VendorIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    gstin: Optional[str] = None
    pan: Optional[str] = None
    #: 0 is "due on receipt" — a real term. Only ``None`` means unknown.
    payment_terms_days: Optional[int] = None
    status: CustomerStatus = CustomerStatus.ACTIVE
    source_ref: SourceRef


class StockSnapshotIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    product_external_id: str = Field(min_length=1)
    as_of: date
    on_hand: Optional[Decimal] = None
    available: Optional[Decimal] = None
    actual_available: Optional[Decimal] = None
    #: ``None`` where Zoho holds a blank. Never coerced to 0 — "no reorder
    #: point set" and "reorder at zero" are different statements and only one
    #: of them is a policy somebody chose.
    reorder_level: Optional[Decimal] = None
    purchase_rate: Optional[Decimal] = None
    tracked: bool = True
    source_ref: SourceRef

    @field_validator("on_hand", "available", "actual_available", "reorder_level",
                     "purchase_rate", mode="before")
    @classmethod
    def _blank_is_unknown(cls, v: Any) -> Optional[Decimal]:
        # Zoho sends "" for an unset numeric field. Decimal("") raises, and
        # float("" or 0) would quietly turn "unset" into zero.
        if v is None or v == "":
            return None
        return Decimal(str(v))


class PaymentApplicationIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    invoice_external_ref: str = Field(min_length=1)
    invoice_number: Optional[str] = None
    invoice_date: date
    invoice_due_date: Optional[date] = None
    amount_applied: Decimal

    @field_validator("amount_applied", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v if v not in (None, "") else 0))


class PaymentReceiptIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    customer_external_id: str = Field(min_length=1)
    date: date
    amount: Decimal
    mode: Optional[str] = None
    is_advance: bool = False
    unapplied_amount: Optional[Decimal] = None
    applications: list[PaymentApplicationIn] = Field(default_factory=list)
    source_ref: SourceRef

    @field_validator("amount", "unapplied_amount", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class SalesOrderIn(BaseModel):
    """One customer order, header grain. The demand-side mirror of
    ``PurchaseOrderIn`` and deliberately the same shape."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    customer_external_id: Optional[str] = None
    date: date
    expected_ship_date: Optional[date] = None
    status: str = ""
    invoiced_status: Optional[str] = None
    shipped_status: Optional[str] = None
    total: Optional[Decimal] = None
    salesperson_external_id: Optional[str] = None
    source_ref: SourceRef

    @field_validator("total", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        # Via str, so a float cannot introduce binary noise into a figure the
        # commitment layer will add up.
        return v if isinstance(v, Decimal) else Decimal(str(v))


class BillIn(BaseModel):
    """A bill's payable terms, header grain. The companion to the
    ``CostRecordIn`` list the same document produces."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    vendor_external_id: Optional[str] = None
    date: date
    due_date: Optional[date] = None
    status: str = ""
    total: Optional[Decimal] = None
    #: What Zoho says is still owed. ``None`` where the field is absent —
    #: never coerced to 0, which would read as "settled".
    balance: Optional[Decimal] = None
    source_ref: SourceRef

    @field_validator("total", "balance", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class InvoiceIn(BaseModel):
    """An invoice's receivable terms, header grain. The mirror of ``BillIn``.

    Deliberately the same shape: one is what a supplier is owed, the other what
    a customer owes us, and the two answer the same question in opposite
    directions. A different shape for the receivable side would mean two ways to
    ask "what is outstanding and how overdue".

    The companion to the ``SalesTxnIn`` list the same document produces —
    ``balance`` and ``due_date`` are facts about one invoice, and copying them
    onto every line would make "what is outstanding" a de-duplication problem
    instead of a sum.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    customer_external_id: Optional[str] = None
    date: date
    due_date: Optional[date] = None
    status: str = ""
    total: Optional[Decimal] = None
    #: What Zoho says is still owed on this invoice. ``None`` where the field is
    #: absent — never coerced to 0, which would read as "collected".
    balance: Optional[Decimal] = None
    source_ref: SourceRef

    @field_validator("total", "balance", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class VendorPaymentIn(BaseModel):
    """One payment out. Amount is required — a payment with no amount is not a
    payment, and defaulting it to zero would understate cash out silently."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    vendor_external_id: Optional[str] = None
    date: date
    amount: Decimal
    mode: Optional[str] = None
    reference: Optional[str] = None
    source_ref: SourceRef

    @field_validator("amount", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))


class PurchaseOrderIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    vendor_external_id: Optional[str] = None
    date: date
    expected_date: Optional[date] = None
    status: str = ""
    received_status: Optional[str] = None
    ordered_qty: Optional[Decimal] = None
    pending_qty: Optional[Decimal] = None
    total: Optional[Decimal] = None
    received_on: Optional[date] = None
    source_ref: SourceRef

    @field_validator("ordered_qty", "pending_qty", "total", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class DecisionRead(BaseModel):
    """Scope-filtered decision projection returned by the API."""

    decision_id: str
    organization_id: str
    decision_type: str
    subject_entity_type: str
    subject_entity_id: str
    assigned_user_id: Optional[str]
    assigned_role: str
    detected_at: datetime
    priority_band: str
    priority_score: int
    status: str
    ai_status: str
    human_action: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    #: Which connected company this decision's *subject* belongs to. Not
    #: ``origin``: a decision's origin is whether it was folded from state or
    #: raised from a signal, and one key cannot carry both meanings. Optional
    #: because a decision fetched on its own does not pay for the master index
    #: the list loads once; ``None`` means "not resolved here", which the screen
    #: renders as nothing rather than as "unknown".
    subject_origin: Optional[dict[str, Any]] = None
    #: True when the organization reads more than one connected company. Below
    #: two, every badge would say the same thing.
    sources_differ: bool = False


class ActionRequest(BaseModel):
    action: str                                       # VIEW | ACT | DISMISS | SNOOZE | OVERRIDE
    note: Optional[str] = None
    reason: Optional[str] = None
