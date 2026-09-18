"""Microsoft Dynamics 365 Business Central, driven over its own OData rows.

The stub answers ``pages`` by entity set. Rows carry the v2.0 resource
properties the source actually reads — ``salesInvoiceLines`` expanded inline,
``lineType`` deciding which lines count, ``amountExcludingTax`` as the
post-discount line amount.

Money is stated as strings. Business Central really sends JSON numbers, and
stating them as strings here is deliberate: it isolates what this connector
*computes* from what it merely carries, which is the only way the float check
can say anything about the translator.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from app.ingestion.erp import dynamics365

COMPANY = "11111111-2222-3333-4444-555555555555"

CUSTOMERS = [{"id": "c-0001", "number": "C-0001", "displayName": "Vaya Precision LLC",
              "blocked": " "}]

VENDORS = [{"id": "v-0001", "number": "V-0001", "displayName": "Kennametal Inc",
            "blocked": " "}]

ITEMS = [{"id": "i-0001", "number": "CNMG120408-MP", "displayName": "CNMG 120408 MP",
          "type": "Inventory", "baseUnitOfMeasureCode": "PCS",
          "itemCategoryCode": "INSERTS", "unitCost": "158.00", "blocked": False,
          "inventory": "240"}]

#: ``unitPrice`` is the list rate and ``amountExcludingTax`` is what is left
#: after the line discount — the discounted line the net-of-discount rule needs.
SALES_INVOICES = [{
    "id": "si-0001", "number": "INV-1042", "customerId": "c-0001",
    "customerName": "Vaya Precision LLC", "invoiceDate": "2026-06-04",
    "dueDate": "2026-07-04", "status": "Open",
    "totalAmountExcludingTax": "2250.00", "remainingAmount": "2250.00",
    "currencyCode": "USD", "lastModifiedDateTime": "2026-06-05T11:02:00Z",
    "salesInvoiceLines": [{
        "id": "sil-1", "sequence": 10000, "lineType": "Item", "itemId": "i-0001",
        "quantity": "10", "unitPrice": "250.00", "amountExcludingTax": "2250.00",
        "discountAmount": "250.00", "description": "CNMG 120408 MP"}],
}]

PURCHASE_INVOICES = [{
    "id": "pi-0001", "number": "PINV-556", "vendorInvoiceNumber": "KMT-99812",
    "vendorId": "v-0001", "vendorName": "Kennametal Inc",
    "invoiceDate": "2026-05-18", "dueDate": "2026-06-17", "status": "Open",
    "totalAmountExcludingTax": "1710.00", "remainingAmount": "1710.00",
    "currencyCode": "USD", "lastModifiedDateTime": "2026-05-19T08:30:00Z",
    "purchaseInvoiceLines": [{
        "id": "pil-1", "sequence": 10000, "lineType": "Item", "itemId": "i-0001",
        "quantity": "12", "unitCost": "158.00", "amountExcludingTax": "1710.00",
        "discountAmount": "186.00", "description": "CNMG 120408 MP"}],
}]

SALES_ORDERS = [{"id": "so-0001", "number": "SO-2201", "customerId": "c-0001",
                 "orderDate": "2026-05-02", "requestedDeliveryDate": "2026-05-30",
                 "status": "Open", "fullyShipped": False,
                 "totalAmountExcludingTax": "2250.00", "currencyCode": "USD"}]

PURCHASE_ORDERS = [{"id": "po-0001", "number": "PO-9931", "vendorId": "v-0001",
                    "orderDate": "2026-04-28", "requestedReceiptDate": "2026-05-15",
                    "status": "Open", "fullyReceived": True,
                    "totalAmountExcludingTax": "1710.00"}]

_PAGES = {"customers": CUSTOMERS, "vendors": VENDORS, "items": ITEMS,
          "salesInvoices": SALES_INVOICES, "purchaseInvoices": PURCHASE_INVOICES,
          "salesOrders": SALES_ORDERS, "purchaseOrders": PURCHASE_ORDERS}


class _Client:
    def pages(self, path: str, *, company_id: Optional[str] = None,
              params: Optional[dict[str, Any]] = None) -> Iterator[dict[str, Any]]:
        rows = _PAGES.get(path.strip("/"))
        if rows is None:
            raise AssertionError(f"the Business Central fixture has no rows for: {path}")
        return iter(rows)


def build_source() -> dynamics365.BusinessCentralSource:
    return dynamics365.BusinessCentralSource(_Client(), COMPANY)
