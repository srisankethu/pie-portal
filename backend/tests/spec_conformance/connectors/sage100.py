"""Sage 100, driven over its own SData resources.

The client flattens each Atom entry to localname -> text before the source sees
it, so every value here is a string — which Sage 100 genuinely sends, and which
also gives the float check something to mean.

Parties are keyed on the (division, number) pair, and the invoice history keys
on (InvoiceNo, HeaderSeqNo); both are reproduced so the identity check reads
the real composite rather than a simplified one.
"""
from __future__ import annotations

from typing import Iterator

from app.ingestion.erp import sage

CUSTOMERS = [{"ARDivisionNo": "01", "CustomerNo": "VAYA01",
              "CustomerName": "Vaya Precision LLC", "InactiveCustomer": "N"}]

VENDORS = [{"APDivisionNo": "01", "VendorNo": "KMT001",
            "VendorName": "Kennametal Inc"}]

ITEMS = [{"ItemCode": "CNMG120408-MP", "ItemCodeDesc": "CNMG 120408 MP",
          "StandardUnitOfMeasure": "EACH", "ProductLine": "INSERTS",
          "ProductType": "1", "InactiveItem": "N"}]

#: ``UnitPrice`` is the list rate and ``ExtensionAmt`` the line's amount after
#: the discount — the discounted line the net-of-discount rule needs.
INVOICE_HEADERS = [{
    "InvoiceNo": "0100412", "HeaderSeqNo": "000", "ARDivisionNo": "01",
    "CustomerNo": "VAYA01", "BillToName": "Vaya Precision LLC",
    "InvoiceDate": "2026-06-04", "InvoiceDueDate": "2026-07-04",
    "InvoiceTotal": "2250.00", "Balance": "2250.00",
    "DateUpdated": "2026-06-05", "TransactionDate": "2026-06-04",
    # The internal-control audit pair, as Sage 100 splits it: a date
    # column and a time column, neither carrying a zone.
    "DateCreated": "2026-06-04", "TimeCreated": "09:14",
}]
INVOICE_LINES = [{"InvoiceNo": "0100412", "DetailSeqNo": "000001",
                  "ItemCode": "CNMG120408-MP", "ItemCodeDesc": "CNMG 120408 MP",
                  "QuantityShipped": "10", "UnitPrice": "250.00",
                  "ExtensionAmt": "2250.00"}]

SALES_ORDERS = [{"SalesOrderNo": "0005510", "ARDivisionNo": "01",
                 "CustomerNo": "VAYA01", "OrderDate": "2026-05-02",
                 "ShipExpireDate": "2026-05-30", "OrderStatus": "O"}]

PURCHASE_ORDERS = [{"PurchaseOrderNo": "0003310", "APDivisionNo": "01",
                    "VendorNo": "KMT001", "PurchaseOrderDate": "2026-04-28",
                    "RequiredExpireDate": "2026-05-15", "OrderStatus": "O"}]

_RESOURCES = {"AR_Customer": CUSTOMERS, "AP_Vendor": VENDORS, "CI_Item": ITEMS,
              "AR_InvoiceHistoryHeader": INVOICE_HEADERS,
              "AR_InvoiceHistoryDetail": INVOICE_LINES,
              "SO_SalesOrderHeader": SALES_ORDERS,
              "PO_PurchaseOrderHeader": PURCHASE_ORDERS}


class _Client:
    def resources(self, name: str) -> Iterator[dict[str, str]]:
        rows = _RESOURCES.get(name)
        if rows is None:
            raise AssertionError(f"the Sage 100 fixture has no rows for: {name}")
        return iter(rows)


def build_source() -> sage.Sage100Source:
    return sage.Sage100Source(_Client())
