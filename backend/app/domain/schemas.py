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
    """A pointer back to the originating ERP record (provenance).

    ``system`` is required, and that is the point. It used to default to
    ``"zoho"``, which meant no normalizer ever had to say where a record came
    from — and every row in the database claimed Zoho whether or not it was
    true. A second connector would have had to *remember* to override it, and
    forgetting would have been silent: the evidence a decision cites would name
    the wrong system, and nothing anywhere would disagree.

    Provenance that defaults is not provenance. This is the layer that must not
    know which ERP exists, so the value comes from the adapter that read the
    record.
    """

    system: str = Field(min_length=1)
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
    #: Who makes the item, likewise raw. See ``models.Product.manufacturer``
    #: for why this is not a vendor.
    manufacturer: Optional[str] = None
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


class DocumentApplicationIn(BaseModel):
    """One payment set against one document, in either direction.

    Named for the document rather than for the invoice because the payable side
    carries exactly this shape: a payment out, the bill it settled, and that
    bill's own date and terms. The two tables it lands in keep their own column
    names (``invoice_date`` / ``bill_date``), because a released column is not
    worth renaming to match a DTO — but there is one description of what an
    application *is*, so the two sides cannot drift on which dates they carry.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    document_external_ref: str = Field(min_length=1)
    document_number: Optional[str] = None
    #: When the document was raised. Required: without it there is no
    #: days-to-pay to compute, and defaulting it to the payment date would
    #: manufacture a book that always settles same-day.
    document_date: date
    #: When it fell due. Absent on documents raised without terms, which makes
    #: "days late" unanswerable for them — never zero.
    document_due_date: Optional[date] = None
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
    applications: list[DocumentApplicationIn] = Field(default_factory=list)
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


class InvoiceSalesOrderRef(BaseModel):
    """One order an invoice bills against, as the invoice itself names it.

    A list of these rather than a scalar on ``InvoiceIn`` because the
    relationship is many-to-many in this book — see
    ``models.InvoiceSalesOrderLink``. ``is_primary`` carries which one Zoho's
    scalar ``salesorder_id`` named, which is a fact about the payload rather
    than about the trade.
    """

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    is_primary: bool = False


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
    #: Every order this invoice bills against. Empty is legitimate and common —
    #: stock sold across the counter has no order behind it — and it means the
    #: order-to-invoice lag for this invoice is unknown, never zero.
    sales_orders: list[InvoiceSalesOrderRef] = Field(default_factory=list)
    source_ref: SourceRef

    @field_validator("total", "balance", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class LocationIn(BaseModel):
    """One place the business trades from. Zoho calls these locations; the books
    call them branches, and the invoice payload carries both names for the same
    id.

    Not merely a label. Head Office and the Bangalore branch hold **separate GST
    registrations**, which is what makes "which branch earned this" a real
    question rather than a reporting preference.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    name: str
    #: Zoho's own kind. ``general`` is a place that trades; ``line_item_only``
    #: is a store that can appear on a document line but is not a branch in its
    #: own right. Kept raw and interpreted at read time, the same discipline
    #: ``Product.category`` uses — mapping this onto "is a branch" is policy.
    kind: Optional[str] = None
    #: Zoho's parent/child nesting: a godown under a head office. Carried so a
    #: roll-up can be correct rather than double-counting a child into a total
    #: that already includes it.
    parent_external_ref: Optional[str] = None
    is_active: bool = True
    is_primary: bool = False
    #: The GSTIN registered at this location, where there is one. The strongest
    #: evidence that a location is a real trading entity rather than a shelf.
    tax_reg_no: Optional[str] = None
    source_ref: SourceRef


class StockLocationSnapshotIn(BaseModel):
    """What one item held at one location, on one day.

    **Deliberately not columns on ``StockSnapshotIn``.** That record is
    organization-grain — one row per item per day — and several readers count on
    exactly that. Adding a location column would turn one row into one row per
    location and silently multiply every existing total. Two grains, two
    records, the same reason ``InvoiceDoc`` sits beside ``SalesTxn``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    product_external_id: str = Field(min_length=1)
    location_external_ref: str = Field(min_length=1)
    as_of: date
    on_hand: Optional[Decimal] = None
    available: Optional[Decimal] = None
    #: What Zoho values this location's holding at. Cost — manager scope only,
    #: and the numerator of nothing a salesperson may see.
    asset_value: Optional[Decimal] = None
    source_ref: SourceRef

    @field_validator("on_hand", "available", "asset_value", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class CreditNoteIn(BaseModel):
    """A credit note's header. The mirror of ``InvoiceIn``, opposite sign.

    Deliberately the same shape as the receivable it reduces: an invoice is what
    a customer owes, a credit note is what was given back, and two different
    shapes for one ledger would mean two ways to ask what a customer's position
    actually is.

    ``balance`` here is what remains *unapplied* — credit the customer holds but
    which has not yet been set against any invoice. It is passed through exactly
    as Zoho states it, never derived from ``total`` minus the applications read
    below, because a refund against the credit note would make that subtraction
    wrong in the direction that overstates the credit still available.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    number: Optional[str] = None
    customer_external_id: Optional[str] = None
    date: date
    status: str = ""
    total: Optional[Decimal] = None
    #: Credit raised but not yet applied to any invoice. ``None`` where Zoho did
    #: not say — never coerced to 0, which would read as "fully applied".
    balance: Optional[Decimal] = None
    source_ref: SourceRef

    @field_validator("total", "balance", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class CreditNoteApplicationIn(BaseModel):
    """One credit note set against one invoice, on one date.

    The grain a historical receivable is reconstructed at, and the reason this
    table exists at all: today's outstanding balance already nets applied credit
    (Zoho states it and ``state/reducers/receivables`` reads it), but "what was
    owed on 31 March" cannot be answered from a balance that only describes now.
    That answer is invoices raised, minus receipts applied, minus *this*.

    ``invoice_date`` is carried here rather than joined from ``InvoiceDoc``, for
    the same reason ``PaymentApplication`` carries it: a credit note landing
    today may settle an invoice raised before the sync window starts, and a join
    would silently drop exactly the oldest positions.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)
    credit_note_external_ref: str = Field(min_length=1)
    customer_external_id: Optional[str] = None
    invoice_external_ref: str = Field(min_length=1)
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    applied_on: date
    amount_applied: Decimal
    source_ref: SourceRef

    @field_validator("amount_applied", mode="before")
    @classmethod
    def _amount_to_decimal(cls, v: Any) -> Decimal:
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
    #: Which bills this payment settled. The same shape the receivable side
    #: carries, so how long *we* take to pay is measured from the same kind of
    #: row — and by the same code — as how long our customers take.
    applications: list[DocumentApplicationIn] = Field(default_factory=list)
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
