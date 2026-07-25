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
from typing import Any

from ..domain.enums import CustomerStatus
from ..domain.schemas import CostRecordIn, CustomerIn, ProductIn, SalesTxnIn, SourceRef


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


def normalize_bill(raw: dict[str, Any]) -> list[CostRecordIn]:
    """One bill → one CostRecordIn per line item (bill-line grain)."""
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
        out.append(CostRecordIn(
            external_ref=f"{bill_id}:{line_id}",
            product_external_id=product_ext,
            date=when, qty=qty, unit_cost=rate,
            source_ref=SourceRef(record_type="bill", record_id=bill_id, line_id=line_id),
        ))
    return out
