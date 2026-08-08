"""Pure adaptation: raw Zoho Books payloads → validated canonical DTOs.

Deterministic and side-effect free. Source data is preserved (external ids +
``source_ref`` retained); values are never invented beyond a documented,
deterministic fallback (line_revenue = qty × rate when the source omits a line
total). Malformed/missing rows raise :class:`NormalizationError`, which the sync
layer records and skips — never a silent drop, never a silent mutation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from ..domain.enums import CustomerStatus
from ..domain.schemas import (BillIn, CostRecordIn, CustomerIn, InvoiceIn,
                             PaymentApplicationIn, PaymentReceiptIn, ProductIn,
                             PurchaseOrderIn, SalesOrderIn, SalesTxnIn, SourceRef,
                             StockSnapshotIn, VendorIn, VendorPaymentIn)

#: The system every record in this module came from. Stated once, and stated
#: *here* rather than defaulted in ``SourceRef``, because this file is the Zoho
#: adapter — it is the only layer entitled to know that. A second adapter names
#: itself the same way, and neither can silently inherit the other's answer.
ZOHO = "zoho"

_HUNDRED = Decimal("100")


class NormalizationError(ValueError):
    """A source record could not be adapted. Carries a machine code + detail."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _require(raw: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise NormalizationError("MISSING_FIELD", f"{ctx}: missing '{key}'")
    return raw[key]


def _parse_date(value: Any, ctx: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise NormalizationError("BAD_DATE", f"{ctx}: unparseable date {value!r}")


def _parse_decimal(value: Any, ctx: str, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise NormalizationError("BAD_NUMBER", f"{ctx}: {field} not numeric ({value!r})")


def normalize_customer(raw: dict[str, Any]) -> CustomerIn:
    cid = _require(raw, "contact_id", "contact")
    status = str(raw.get("status", "active")).lower()
    return CustomerIn(
        external_id=str(cid),
        name=str(_require(raw, "contact_name", "contact")),
        status=CustomerStatus.ACTIVE if status == "active" else CustomerStatus.INACTIVE,
        source_ref=SourceRef(system=ZOHO, record_type="contact", record_id=str(cid)),
    )


def normalize_product(raw: dict[str, Any]) -> ProductIn:
    iid = _require(raw, "item_id", "item")
    status = str(raw.get("status", "active")).lower()
    return ProductIn(
        external_id=str(iid),
        name=str(_require(raw, "name", "item")),
        uom=(str(raw["unit"]) if raw.get("unit") else None),
        hsn=(str(raw["hsn_or_sac"]) if raw.get("hsn_or_sac") else None),
        category=(str(raw["category_name"]) if raw.get("category_name") else None),
        brand=(str(raw["manufacturer"]) if raw.get("manufacturer") else None),
        active=(status == "active"),
        source_ref=SourceRef(system=ZOHO, record_type="item", record_id=str(iid)),
    )


def normalize_invoice(raw: dict[str, Any]) -> list[SalesTxnIn]:
    """One invoice → one SalesTxnIn per line item (invoice-line grain).

    ``unit_price`` is the *net* selling price — after the line discount, which
    is what the customer actually paid and the only figure a margin may be
    computed from. ``rate`` (the original pre-discount list price) and
    ``discount_percent`` are preserved alongside it for audit; nothing
    downstream should compute economics from them.
    """
    inv_id = str(_require(raw, "invoice_id", "invoice"))
    customer_ext = str(_require(raw, "customer_id", f"invoice {inv_id}"))
    when = _parse_date(_require(raw, "date", f"invoice {inv_id}"), f"invoice {inv_id}")
    lines = raw.get("line_items") or []
    if not lines:
        raise NormalizationError("NO_LINES", f"invoice {inv_id}: no line_items")
    out: list[SalesTxnIn] = []
    for i, ln in enumerate(lines):
        line_id = str(ln.get("line_item_id") or i)
        ctx = f"invoice {inv_id} line {line_id}"
        product_ext = str(_require(ln, "item_id", ctx))
        qty = _parse_decimal(_require(ln, "quantity", ctx), ctx, "quantity")
        rate = _parse_decimal(_require(ln, "rate", ctx), ctx, "rate")
        net_price, discount_pct = _effective_unit_amount(rate, qty, ln, ctx)
        # Prefer the source line total; fall back to qty×net deterministically.
        # Both are post-discount, so revenue and unit_price cannot disagree.
        revenue = (_parse_decimal(ln["item_total"], ctx, "item_total")
                   if ln.get("item_total") not in (None, "") else qty * net_price)
        out.append(SalesTxnIn(
            external_ref=f"{inv_id}:{line_id}",
            customer_external_id=customer_ext,
            product_external_id=product_ext,
            date=when, qty=qty, unit_price=net_price, line_revenue=revenue,
            rate=rate, discount_percent=discount_pct,
            source_ref=SourceRef(system=ZOHO, record_type="invoice", record_id=inv_id, line_id=line_id),
        ))
    return out


def _parse_percent(value: Any, ctx: str, field: str) -> Decimal:
    """A Zoho line-item discount: either a bare number (percent) or a string
    like "50%" — both forms appear across Books API versions/organizations."""
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise NormalizationError("BAD_DISCOUNT", f"{ctx}: {field} not numeric ({value!r})")


def _effective_unit_amount(rate: Decimal, qty: Decimal, ln: dict[str, Any],
                           ctx: str) -> tuple[Decimal, Optional[Decimal]]:
    """The actual per-unit amount after the line's discount, plus the discount
    percent for audit.

    Shared by bills (purchase cost) and invoices (net selling price) — the
    discount shapes Zoho emits are identical on both sides, and so is the
    correct resolution. ``rate * (1 - discount% / 100)`` is only the fallback;
    Zoho's own resolved values are preferred whenever present:

    1. ``item_total`` — the line's post-discount, pre-tax total. Most
       authoritative: Zoho has already resolved whatever discount shape it used.
    2. ``discount_amount`` — Zoho's own resolved monetary discount for the line,
       sidestepping whether ``discount`` itself is a percentage or an amount.
    3. ``discount`` — parsed as a percentage of ``rate`` (the documented formula).
    4. Nothing present — no discount; the amount equals the list rate.

    Taxes and document-level (non-line) adjustments are deliberately not
    touched: the existing definition of both cost and revenue here has always
    been pre-tax, and this does not change that.
    """
    item_total, discount_amount, discount_raw = (
        ln.get("item_total"), ln.get("discount_amount"), ln.get("discount"))
    discount_pct: Optional[Decimal] = None

    if item_total not in (None, "") and qty > 0:
        amount = _parse_decimal(item_total, ctx, "item_total") / qty
    elif discount_amount not in (None, "") and qty > 0:
        off = _parse_decimal(discount_amount, ctx, "discount_amount")
        amount = rate - (off / qty)
    elif discount_raw not in (None, ""):
        discount_pct = _parse_percent(discount_raw, ctx, "discount")
        if discount_pct < 0 or discount_pct > _HUNDRED:
            raise NormalizationError(
                "BAD_DISCOUNT", f"{ctx}: discount {discount_pct}% out of range [0, 100]")
        amount = rate * (Decimal("1") - discount_pct / _HUNDRED)
    else:
        amount = rate

    if amount < 0:
        raise NormalizationError(
            "BAD_DISCOUNT", f"{ctx}: resolved a negative unit amount ({amount})")

    if discount_pct is None and rate > 0:
        # Derive the audit percent from whichever authoritative value supplied
        # the amount, so it stays consistent regardless of which Zoho field it
        # came from — this is what lets the platform later say "rate ₹3,166,
        # discount 50%, effective ₹1,583" no matter which path computed it.
        discount_pct = (Decimal("1") - amount / rate) * _HUNDRED

    return amount, discount_pct


def normalize_bill(raw: dict[str, Any]) -> list[CostRecordIn]:
    """One bill → one CostRecordIn per line item (bill-line grain).

    ``unit_cost`` is the *effective*, post-discount cost — what every margin,
    pricing and decision calculation must read. ``rate`` (the original,
    pre-discount list rate) and ``discount_percent`` are preserved alongside it
    purely for audit; nothing downstream should compute from them.
    """
    bill_id = str(_require(raw, "bill_id", "bill"))
    when = _parse_date(_require(raw, "date", f"bill {bill_id}"), f"bill {bill_id}")
    # From the header, onto every line. Not required: a bill from a supplier the
    # vendor pull did not return is still a cost, and dropping the line would
    # understate what an item cost in order to say who sold it.
    vendor_ext = str(raw["vendor_id"]) if raw.get("vendor_id") else None
    lines = raw.get("line_items") or []
    if not lines:
        raise NormalizationError("NO_LINES", f"bill {bill_id}: no line_items")
    out: list[CostRecordIn] = []
    for i, ln in enumerate(lines):
        line_id = str(ln.get("line_item_id") or i)
        ctx = f"bill {bill_id} line {line_id}"
        product_ext = str(_require(ln, "item_id", ctx))
        qty = _parse_decimal(_require(ln, "quantity", ctx), ctx, "quantity")
        rate = _parse_decimal(_require(ln, "rate", ctx), ctx, "rate")
        unit_cost, discount_pct = _effective_unit_amount(rate, qty, ln, ctx)
        out.append(CostRecordIn(
            external_ref=f"{bill_id}:{line_id}",
            product_external_id=product_ext,
            vendor_external_id=vendor_ext,
            date=when, qty=qty, unit_cost=unit_cost, rate=rate,
            discount_percent=discount_pct,
            source_ref=SourceRef(system=ZOHO, record_type="bill", record_id=bill_id, line_id=line_id),
        ))
    return out


# ── supply, stock and cash ───────────────────────────────────────────────────


def normalize_vendor(raw: dict[str, Any]) -> VendorIn:
    vid = _require(raw, "contact_id", "vendor")
    status = str(raw.get("status", "active")).lower()
    terms = raw.get("payment_terms")
    return VendorIn(
        external_id=str(vid),
        name=str(_require(raw, "contact_name", "vendor")),
        gstin=(str(raw["gst_no"]) if raw.get("gst_no") else None),
        pan=(str(raw["pan_no"]) if raw.get("pan_no") else None),
        # 0 days is "due on receipt", a term somebody agreed. Only a genuinely
        # absent value is unknown, so the test is against None and "" — not
        # falsiness, which would erase every due-on-receipt supplier.
        payment_terms_days=(int(terms) if terms not in (None, "") else None),
        status=CustomerStatus.ACTIVE if status == "active" else CustomerStatus.INACTIVE,
        source_ref=SourceRef(system=ZOHO, record_type="vendor", record_id=str(vid)),
    )


def normalize_stock(raw: dict[str, Any], as_of: date) -> StockSnapshotIn:
    """The stock fields riding along on the item payload.

    ``as_of`` is passed in rather than read from the clock here so a whole pull
    lands on one date: an item list that takes four minutes must not produce
    two snapshot days because it crossed midnight halfway through.
    """
    iid = _require(raw, "item_id", "item stock")
    item_type = str(raw.get("item_type") or "").lower()
    return StockSnapshotIn(
        product_external_id=str(iid),
        as_of=as_of,
        on_hand=raw.get("stock_on_hand"),
        available=raw.get("available_stock"),
        actual_available=raw.get("actual_available_stock"),
        reorder_level=raw.get("reorder_level"),
        purchase_rate=raw.get("purchase_rate"),
        # A service has no shelf. Counting it as "zero on hand" would put every
        # service line in the out-of-stock list forever.
        tracked=bool(raw.get("track_inventory")) and item_type != "service",
        source_ref=SourceRef(system=ZOHO, record_type="item", record_id=str(iid)),
    )


def normalize_payment(raw: dict[str, Any]) -> PaymentReceiptIn:
    pid = _require(raw, "payment_id", "payment")
    ctx = f"payment {pid}"
    applications: list[PaymentApplicationIn] = []
    for a in raw.get("invoices") or []:
        invoice_id = str(a.get("invoice_id") or "")
        if not invoice_id:
            continue
        raw_date = a.get("date")
        if not raw_date:
            # Without the invoice's own date there is no days-to-pay to
            # compute. Dropping the application is right; defaulting it to the
            # payment date would manufacture a book that always pays same-day.
            continue
        due = a.get("due_date")
        applications.append(PaymentApplicationIn(
            external_ref=str(a.get("invoice_payment_id") or f"{pid}:{invoice_id}"),
            invoice_external_ref=invoice_id,
            invoice_number=(str(a["invoice_number"]) if a.get("invoice_number") else None),
            invoice_date=_parse_date(raw_date, ctx),
            invoice_due_date=(_parse_date(due, ctx) if due else None),
            amount_applied=a.get("amount_applied"),
        ))
    return PaymentReceiptIn(
        external_ref=str(pid),
        customer_external_id=str(_require(raw, "customer_id", ctx)),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        amount=raw.get("amount"),
        mode=(str(raw["payment_mode"]) if raw.get("payment_mode") else None),
        is_advance=bool(raw.get("is_advance_payment")),
        unapplied_amount=raw.get("unused_amount"),
        applications=applications,
        source_ref=SourceRef(system=ZOHO, record_type="customerpayment", record_id=str(pid)),
    )


def normalize_sales_order(raw: dict[str, Any]) -> SalesOrderIn:
    """One customer order. Header grain, mirroring ``normalize_purchase_order``.

    ``expected_ship_date`` is left as ``None`` when Zoho has none rather than
    derived from the order date plus an assumed lead time: "we did not promise a
    date" and "we promised this date" are different facts, and only one of them
    can make an order late.
    """
    soid = _require(raw, "salesorder_id", "sales order")
    ctx = f"sales order {soid}"
    ship = raw.get("shipment_date")
    return SalesOrderIn(
        external_ref=str(soid),
        number=(str(raw["salesorder_number"]) if raw.get("salesorder_number") else None),
        customer_external_id=(str(raw["customer_id"]) if raw.get("customer_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        expected_ship_date=(_parse_date(ship, ctx) if ship else None),
        status=str(raw.get("status") or ""),
        invoiced_status=(str(raw["invoiced_status"]) if raw.get("invoiced_status") else None),
        shipped_status=(str(raw["shipped_status"]) if raw.get("shipped_status") else None),
        total=raw.get("total"),
        salesperson_external_id=(str(raw["salesperson_id"])
                                 if raw.get("salesperson_id") else None),
        source_ref=SourceRef(system=ZOHO, record_type="salesorder", record_id=str(soid)),
    )


def normalize_bill_terms(raw: dict[str, Any]) -> BillIn:
    """The payable header of a bill, from the payload ``normalize_bill``
    already receives.

    Separate from ``normalize_bill`` rather than folded into it because the two
    have different grains and different failure modes: a bill with no line
    items is useless as cost and is rejected there, but it is still money owed
    and must survive here. Returning a tuple from one function would tie the
    fates of both together.

    ``balance`` is passed through as Zoho states it. Deriving it from ``total``
    minus payments read elsewhere would be wrong the moment a credit note is
    applied to the bill, and wrong in the direction that overstates what is
    owed.
    """
    bill_id = str(_require(raw, "bill_id", "bill"))
    ctx = f"bill {bill_id}"
    due = raw.get("due_date")
    return BillIn(
        external_ref=bill_id,
        number=(str(raw["bill_number"]) if raw.get("bill_number") else None),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        # No terms on the bill means it cannot be aged. Left as None rather
        # than defaulted to the bill date, which would report every untermed
        # bill as overdue from the day it was raised.
        due_date=(_parse_date(due, ctx) if due else None),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        source_ref=SourceRef(system=ZOHO, record_type="bill", record_id=bill_id),
    )


def normalize_invoice_terms(raw: dict[str, Any]) -> InvoiceIn:
    """The receivable header of an invoice, from the payload
    ``normalize_invoice`` already receives.

    The mirror of ``normalize_bill_terms``, and separate from
    ``normalize_invoice`` for the same reason: the two have different grains and
    different failure modes. An invoice with no line items is useless as revenue
    and is rejected there, but it is still money owed and must survive here.

    ``balance`` is passed through as Zoho states it. Deriving it from ``total``
    minus receipts read elsewhere would be wrong the moment a credit note is
    applied to the invoice, and wrong in the direction that overstates what is
    collectable — which is the direction that gets somebody chased for money
    they do not owe.
    """
    invoice_id = str(_require(raw, "invoice_id", "invoice"))
    ctx = f"invoice {invoice_id}"
    due = raw.get("due_date")
    return InvoiceIn(
        external_ref=invoice_id,
        number=(str(raw["invoice_number"]) if raw.get("invoice_number") else None),
        customer_external_id=(str(raw["customer_id"]) if raw.get("customer_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        # No terms on the invoice means it cannot be aged. Left as None rather
        # than defaulted to the invoice date, which would report every untermed
        # invoice as overdue from the day it was raised — and put a customer on
        # a collections list for an obligation nobody ever gave them.
        due_date=(_parse_date(due, ctx) if due else None),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        source_ref=SourceRef(system=ZOHO, record_type="invoice", record_id=invoice_id),
    )


def normalize_vendor_payment(raw: dict[str, Any]) -> VendorPaymentIn:
    """One payment out. The amount is required, never defaulted.

    A payment row with no amount is malformed, not a zero-rupee payment, and
    defaulting it would understate cash out by exactly as much as the row was
    worth — silently, and in the direction that flatters liquidity.
    """
    pid = _require(raw, "payment_id", "vendor payment")
    ctx = f"vendor payment {pid}"
    return VendorPaymentIn(
        external_ref=str(pid),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        amount=_parse_decimal(_require(raw, "amount", ctx), ctx, "amount"),
        mode=(str(raw["payment_mode"]) if raw.get("payment_mode") else None),
        reference=(str(raw["reference_number"]) if raw.get("reference_number") else None),
        source_ref=SourceRef(system=ZOHO, record_type="vendorpayment", record_id=str(pid)),
    )


def normalize_purchase_order(raw: dict[str, Any]) -> PurchaseOrderIn:
    poid = _require(raw, "purchaseorder_id", "purchase order")
    ctx = f"purchase order {poid}"
    expected = raw.get("expected_delivery_date")
    # Zoho records receipts as a list; the last one is when the order finished
    # arriving. No receives at all means either still open or never logged —
    # left as None, because those two are different and the screen says so.
    receives = [r.get("date") for r in (raw.get("receives") or []) if r.get("date")]
    received_on = _parse_date(max(receives), ctx) if receives else None
    return PurchaseOrderIn(
        external_ref=str(poid),
        number=(str(raw["purchaseorder_number"]) if raw.get("purchaseorder_number") else None),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        expected_date=(_parse_date(expected, ctx) if expected else None),
        status=str(raw.get("status") or ""),
        received_status=(str(raw["received_status"]) if raw.get("received_status") else None),
        ordered_qty=raw.get("total_ordered_quantity"),
        pending_qty=raw.get("quantity_yet_to_receive"),
        total=raw.get("total"),
        received_on=received_on,
        source_ref=SourceRef(system=ZOHO, record_type="purchaseorder", record_id=str(poid)),
    )
