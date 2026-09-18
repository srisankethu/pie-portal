"""Acumatica, driven over its own contract-based REST rows.

Every scalar arrives wrapped as ``{"value": ...}`` and the source's ``_plain``
undresses it, so the fixture wears the wrapping: a flat row would exercise a
shape this ERP never sends. Money is stated as strings inside that wrapping,
for the reason the other fixtures give.
"""
from __future__ import annotations

from typing import Any, Iterator

from app.ingestion.erp import acumatica


def _v(value: Any) -> dict[str, Any]:
    return {"value": value}


def _row(**fields: Any) -> dict[str, Any]:
    return {key: _v(value) for key, value in fields.items()}


CUSTOMERS = [_row(CustomerID="VAYA", CustomerName="Vaya Precision LLC",
                  CustomerStatus="Active")]

VENDORS = [_row(VendorID="KMT", VendorName="Kennametal Inc", VendorStatus="Active",
                Terms="30")]

ITEMS = [_row(InventoryID="CNMG120408-MP", Description="CNMG 120408 MP",
              BaseUOM="PCS", ItemClass="INSERTS", ItemStatus="Active",
              ItemType="Finished Good", LastCost="158.00", QtyOnHand="240",
              QtyAvailable="210")]

#: ``UnitPrice`` is the list rate; ``Amount`` is the extended amount after the
#: line discount. The gap is the discount the net-of-discount rule reads.
INVOICES = [{
    **_row(ReferenceNbr="AR004412", CustomerID="VAYA",
           CustomerName="Vaya Precision LLC", Date="2026-06-04T00:00:00+00:00",
           DueDate="2026-07-04T00:00:00+00:00", Status="Open", Amount="2250.00",
           Balance="2250.00", CurrencyID="USD",
           LastModifiedDateTime="2026-06-05T11:02:00+00:00",
           CreatedDateTime="2026-06-04T09:14:22+00:00"),
    "Details": [_row(LineNbr="1", InventoryID="CNMG120408-MP", Qty="10",
                     UnitPrice="250.00", Amount="2250.00", DiscountAmount="250.00",
                     TransactionDescription="CNMG 120408 MP")],
}]

BILLS = [{
    **_row(ReferenceNbr="AP009981", VendorRef="KMT-99812", Vendor="KMT",
           Date="2026-05-18T00:00:00+00:00", DueDate="2026-06-17T00:00:00+00:00",
           Status="Open", Amount="1710.00", Balance="1710.00", CurrencyID="USD",
           LastModifiedDateTime="2026-05-19T08:30:00+00:00",
           CreatedDateTime="2026-05-18T07:55:10+00:00"),
    "Details": [_row(LineNbr="1", InventoryID="CNMG120408-MP", Qty="12",
                     UnitCost="158.00", Amount="1710.00", DiscountAmount="186.00",
                     TransactionDescription="CNMG 120408 MP")],
}]

PAYMENTS = [{
    **_row(ReferenceNbr="PMT001120", CustomerID="VAYA",
           ApplicationDate="2026-06-20T00:00:00+00:00", PaymentAmount="2250.00",
           PaymentMethod="ACH", AvailableBalance="0.00",
           LastModifiedDateTime="2026-06-20T14:00:00+00:00",
           CreatedDateTime="2026-06-20T13:59:01+00:00"),
    "DocumentsToApply": [_row(ReferenceNbr="AR004412",
                              DocDate="2026-06-04T00:00:00+00:00",
                              DueDate="2026-07-04T00:00:00+00:00",
                              AmountPaid="2250.00")],
}]

SALES_ORDERS = [_row(OrderNbr="SO005510", CustomerID="VAYA",
                     Date="2026-05-02T00:00:00+00:00",
                     RequestedOn="2026-05-30T00:00:00+00:00", Status="Open",
                     OrderTotal="2250.00", CurrencyID="USD")]

PURCHASE_ORDERS = [_row(OrderNbr="PO003310", VendorID="KMT",
                        Date="2026-04-28T00:00:00+00:00",
                        PromisedOn="2026-05-15T00:00:00+00:00", Status="Open",
                        OrderTotal="1710.00")]

_ENTITIES = {"Customer": CUSTOMERS, "Vendor": VENDORS, "StockItem": ITEMS,
             "SalesInvoice": INVOICES, "Bill": BILLS, "Payment": PAYMENTS,
             "SalesOrder": SALES_ORDERS, "PurchaseOrder": PURCHASE_ORDERS}


class _Client:
    def entities(self, name: str, *, expand: str = "",
                 filter_: str = "") -> Iterator[dict[str, Any]]:
        rows = _ENTITIES.get(name)
        if rows is None:
            raise AssertionError(f"the Acumatica fixture has no rows for: {name}")
        return iter(rows)


def build_source() -> acumatica.AcumaticaSource:
    return acumatica.AcumaticaSource(_Client())
