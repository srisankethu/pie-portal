"""Epicor Prophet 21, driven over its own exposed OData views.

Headers and lines arrive as separate views joined on ``invoice_no``, which is
how the source really reads them, so the stub answers by view name. P21 spells
booleans Y/N and states money as decimal columns; both are reproduced here.

Money is stated as strings for the reason the other fixtures give: a value the
translator passes through stays a string, so a float in the canonical payload
is one this connector computed.
"""
from __future__ import annotations

from typing import Any, Iterator

from app.ingestion.erp import prophet21

CUSTOMERS = [{"customer_id": "10042", "customer_name": "Vaya Precision LLC",
              "delete_flag": "N"}]

SUPPLIERS = [{"supplier_id": "20117", "supplier_name": "Kennametal Inc",
              "delete_flag": "N"}]

ITEMS = [{"inv_mast_uid": "551204", "item_id": "CNMG120408-MP",
          "item_desc": "CNMG 120408 MP", "delete_flag": "N", "inactive": "N"}]

#: ``unit_price`` is the list rate and ``extended_price`` is what was billed
#: after the line discount — the discounted line the net-of-discount rule needs.
INVOICE_HEADERS = [{
    "invoice_no": "IN552031", "customer_id": "10042",
    "invoice_date": "2026-06-04T00:00:00Z", "net_due_date": "2026-07-04T00:00:00Z",
    "total_amount": "2250.00", "amount_paid": "0.00", "delete_flag": "N",
    "date_last_modified": "2026-06-05T11:02:00Z",
    "date_created": "2026-06-04T09:14:22Z",
}]
INVOICE_LINES = [{"invoice_no": "IN552031", "line_no": "1",
                  "inv_mast_uid": "551204", "item_id": "CNMG120408-MP",
                  "qty_shipped": "10", "unit_price": "250.00",
                  "extended_price": "2250.00", "item_desc": "CNMG 120408 MP"}]

BILL_HEADERS = [{
    "apinv_hdr_uid": "880412", "invoice_no": "KMT-99812", "supplier_id": "20117",
    "invoice_date": "2026-05-18T00:00:00Z", "net_due_date": "2026-06-17T00:00:00Z",
    "invoice_amount": "1710.00", "delete_flag": "N",
    "date_last_modified": "2026-05-19T08:30:00Z",
    "date_created": "2026-05-18T07:55:10Z",
}]
BILL_LINES = [{"invoice_no": "KMT-99812", "line_no": "1",
               "inv_mast_uid": "551204", "item_id": "CNMG120408-MP",
               "qty_vouchered": "12", "unit_price": "158.00",
               "extended_price": "1710.00"}]

SALES_ORDERS = [{"order_no": "SO770112", "customer_id": "10042",
                 "order_date": "2026-05-02T00:00:00Z",
                 "requested_date": "2026-05-30T00:00:00Z",
                 "completed": "Y", "cancel_flag": "N"}]

PURCHASE_ORDERS = [{"po_no": "PO330085", "supplier_id": "20117",
                    "order_date": "2026-04-28T00:00:00Z", "complete": "Y"}]

_VIEWS = {"customer": CUSTOMERS, "supplier": SUPPLIERS, "inv_mast": ITEMS,
          "invoice_hdr": INVOICE_HEADERS, "invoice_line": INVOICE_LINES,
          "apinv_hdr": BILL_HEADERS, "apinv_line": BILL_LINES,
          "oe_hdr": SALES_ORDERS, "po_hdr": PURCHASE_ORDERS}


class _Client:
    def view(self, name: str, *, select: str = "",
             filter_: str = "") -> Iterator[dict[str, Any]]:
        rows = _VIEWS.get(name)
        if rows is None:
            raise AssertionError(f"the Prophet 21 fixture has no rows for: {name}")
        return iter(rows)


def build_source() -> prophet21.Prophet21Source:
    return prophet21.Prophet21Source(_Client())
