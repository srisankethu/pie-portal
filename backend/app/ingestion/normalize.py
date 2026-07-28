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
from ..domain.schemas import CostRecordIn, CustomerIn, ProductIn, SalesTxnIn, SourceRef

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
        source_ref=SourceRef(record_type="contact", record_id=str(cid)),
    )


def normalize_product(raw: dict[str, Any]) -> ProductIn:
    iid = _require(raw, "item_id", "item")
    status = str(raw.get("status", "active")).lower()
    return ProductIn(
        external_id=str(iid),
        name=str(_require(raw, "name", "item")),
        uom=(str(raw["unit"]) if raw.get("unit") else None),
        hsn=(str(raw["hsn_or_sac"]) if raw.get("hsn_or_sac") else None),
        active=(status == "active"),
        source_ref=SourceRef(record_type="item", record_id=str(iid)),
    )


def normalize_invoice(raw: dict[str, Any]) -> list[SalesTxnIn]:
    """One invoice → one SalesTxnIn per line item (invoice-line grain)."""
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
        # Prefer the source line total; fall back to qty×rate deterministically.
        revenue = (_parse_decimal(ln["item_total"], ctx, "item_total")
                   if ln.get("item_total") not in (None, "") else qty * rate)
        out.append(SalesTxnIn(
            external_ref=f"{inv_id}:{line_id}",
            customer_external_id=customer_ext,
            product_external_id=product_ext,
            date=when, qty=qty, unit_price=rate, line_revenue=revenue,
            source_ref=SourceRef(record_type="invoice", record_id=inv_id, line_id=line_id),
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


def _effective_unit_cost(rate: Decimal, qty: Decimal, ln: dict[str, Any],
                         ctx: str) -> tuple[Decimal, Optional[Decimal]]:
    """The actual per-unit purchase cost after the line's discount, plus the
    discount percent for audit — ``rate * (1 - discount% / 100)`` is only the
    fallback; Zoho's own resolved values are preferred whenever present:

    1. ``item_total`` — the line's post-discount, pre-tax total. Most
       authoritative: Zoho has already resolved whatever discount shape it used.
    2. ``discount_amount`` — Zoho's own resolved monetary discount for the line,
       sidestepping whether ``discount`` itself is a percentage or an amount.
    3. ``discount`` — parsed as a percentage of ``rate`` (the documented formula).
    4. Nothing present — no discount; cost equals the list rate.

    Taxes and bill-level (non-line) adjustments are deliberately not touched:
    the existing definition of cost here has always been pre-tax, and this fix
    does not change that.
    """
    item_total, discount_amount, discount_raw = (
        ln.get("item_total"), ln.get("discount_amount"), ln.get("discount"))
    discount_pct: Optional[Decimal] = None

    if item_total not in (None, "") and qty > 0:
        unit_cost = _parse_decimal(item_total, ctx, "item_total") / qty
    elif discount_amount not in (None, "") and qty > 0:
        amount = _parse_decimal(discount_amount, ctx, "discount_amount")
        unit_cost = rate - (amount / qty)
    elif discount_raw not in (None, ""):
        discount_pct = _parse_percent(discount_raw, ctx, "discount")
        if discount_pct < 0 or discount_pct > _HUNDRED:
            raise NormalizationError(
                "BAD_DISCOUNT", f"{ctx}: discount {discount_pct}% out of range [0, 100]")
        unit_cost = rate * (Decimal("1") - discount_pct / _HUNDRED)
    else:
        unit_cost = rate

    if unit_cost < 0:
        raise NormalizationError(
            "BAD_DISCOUNT", f"{ctx}: resolved a negative unit cost ({unit_cost})")

    if discount_pct is None and rate > 0:
        # Derive the audit percent from whichever authoritative value supplied
        # the cost, so it stays consistent regardless of which Zoho field it came
        # from — this is what lets the platform later say "rate ₹3,166, discount
        # 50%, effective cost ₹1,583" no matter which path computed the ₹1,583.
        discount_pct = (Decimal("1") - unit_cost / rate) * _HUNDRED

    return unit_cost, discount_pct


def normalize_bill(raw: dict[str, Any]) -> list[CostRecordIn]:
    """One bill → one CostRecordIn per line item (bill-line grain).

    ``unit_cost`` is the *effective*, post-discount cost — what every margin,
    pricing and decision calculation must read. ``rate`` (the original,
    pre-discount list rate) and ``discount_percent`` are preserved alongside it
    purely for audit; nothing downstream should compute from them.
    """
    bill_id = str(_require(raw, "bill_id", "bill"))
    when = _parse_date(_require(raw, "date", f"bill {bill_id}"), f"bill {bill_id}")
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
        unit_cost, discount_pct = _effective_unit_cost(rate, qty, ln, ctx)
        out.append(CostRecordIn(
            external_ref=f"{bill_id}:{line_id}",
            product_external_id=product_ext,
            date=when, qty=qty, unit_cost=unit_cost, rate=rate,
            discount_percent=discount_pct,
            source_ref=SourceRef(record_type="bill", record_id=bill_id, line_id=line_id),
        ))
    return out
