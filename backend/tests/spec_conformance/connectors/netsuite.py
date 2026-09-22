"""Oracle NetSuite, driven over its own SuiteQL rows.

The stub answers ``suiteql`` by the table and transaction type the query names,
because that is what the source really varies between calls. Everything below
is a NetSuite row as the documented columns state one — amounts as strings, GL
signs on the sales side, ``isinactive`` as T/F — so that what the checks see is
the translator's reading of that dress and not a shape invented here.

Money is stated as strings throughout, which is the condition
``harness.money_computed_in_float`` needs to mean anything: a value the
translator passes through stays a string, so a float in the canonical payload
is a number this connector computed.
"""
from __future__ import annotations

from typing import Any, Iterator

from app.ingestion.erp import netsuite

CUSTOMERS = [{"id": "3041", "entityid": "C-3041", "companyname": "Vaya Precision LLC",
              "isinactive": "F"}]

VENDORS = [{"id": "7712", "entityid": "V-7712", "companyname": "Kennametal Inc",
            "isinactive": "F"}]

ITEMS = [{"id": "9155", "itemid": "CNMG120408-MP", "displayname": "CNMG 120408 MP",
          "itemtype": "InvtPart", "isinactive": "F"}]

#: A sales invoice. NetSuite states income lines from the GL's point of view,
#: so quantity and netamount are negative and the translator negates them back.
#: ``netamount`` is 10 per cent under ``rate`` x ``quantity``: the discounted
#: line the net-of-discount check needs, without which it screens nothing.
INVOICE_HEADERS = [{
    "id": "88201", "tranid": "INV-88201", "trandate": "2026-06-04",
    "duedate": "2026-07-04", "entity": "3041", "status": "Paid In Full",
    "foreigntotal": "-2250.00", "foreignamountunpaid": "0.00",
    "currency_code": "USD", "lastmodified": "2026-06-05T11:02:00",
    "createdtime": "2026-06-04T09:14:22",
}]
INVOICE_LINES = [{"tid": "88201", "line_id": "1", "item": "9155",
                  "quantity": "-10", "rate": "250.00", "netamount": "-2250.00",
                  "memo": "CNMG 120408 MP"}]

BILL_HEADERS = [{
    "id": "44107", "tranid": "BILL-44107", "trandate": "2026-05-18",
    "duedate": "2026-06-17", "entity": "7712", "status": "Open",
    "foreigntotal": "1710.00", "foreignamountunpaid": "1710.00",
    "currency_code": "USD", "lastmodified": "2026-05-19T08:30:00",
    "createdtime": "2026-05-18T07:55:10",
}]
BILL_LINES = [{"tid": "44107", "line_id": "1", "item": "9155",
               "quantity": "12", "rate": "158.00", "netamount": "1710.00",
               "memo": "CNMG 120408 MP"}]

PAYMENTS = [{"id": "90310", "trandate": "2026-06-20", "entity": "3041",
             "foreigntotal": "-2250.00", "lastmodified": "2026-06-20T14:00:00",
             "createdtime": "2026-06-20T13:59:01"}]

#: An estimate. ``externalid`` is what the upsert writes and the only field on
#: one that points back at the draft it came from; ``customer_name`` is the
#: BUILTIN.DF the quote query selects and the invoice query does not. The GL
#: sale sign is on ``foreigntotal`` and on the line, as it is for an invoice.
ESTIMATE_HEADERS = [{
    "id": "55120", "tranid": "EST-55120", "externalid": "QB-0042-3f9a1c2e",
    "trandate": "2026-06-01", "entity": "3041", "customer_name": "Vaya Precision LLC",
    "status": "Open", "foreigntotal": "-2250.00", "currency_code": "USD",
    "lastmodified": "2026-06-02T09:15:00", "createdtime": "2026-06-01T08:40:00",
}]
ESTIMATE_LINES = [{"tid": "55120", "line_id": "1", "item": "9155",
                   "quantity": "-10", "rate": "250.00", "netamount": "-2250.00",
                   "memo": "CNMG 120408 MP"}]

SALES_ORDERS = [{"id": "77010", "tranid": "SO-77010", "trandate": "2026-05-02",
                 "shipdate": "2026-05-30", "entity": "3041", "status": "Billed",
                 "foreigntotal": "-2250.00"}]

PURCHASE_ORDERS = [{"id": "66004", "tranid": "PO-66004", "trandate": "2026-04-28",
                    "duedate": "2026-05-15", "entity": "7712", "status": "Fully Billed",
                    "foreigntotal": "1710.00", "foreignamountunpaid": "0.00",
                    "currency_code": "USD", "lastmodified": "2026-05-01T10:00:00",
                    "createdtime": "2026-04-28T06:30:00"}]


class _Client:
    """A NetSuite account that answers queries instead of signing them."""

    def suiteql(self, query: str) -> Iterator[dict[str, Any]]:
        if "FROM customer" in query:
            return iter(CUSTOMERS)
        if "FROM vendor" in query:
            return iter(VENDORS)
        if "FROM item" in query:
            return iter(ITEMS)
        if "FROM transactionline" in query:
            # Keyed on the type the query names rather than on a default, now
            # that three document pulls join lines: an ``else BILL_LINES``
            # fallback would have handed an estimate the bill's lines and the
            # conformance checks would have read a coherent document.
            for marker, rows in (("'CustInvc'", INVOICE_LINES),
                                 ("'Estim'", ESTIMATE_LINES)):
                if marker in query:
                    return iter(rows)
            return iter(BILL_LINES)
        for marker, rows in (("'CustInvc'", INVOICE_HEADERS), ("'VendBill'", BILL_HEADERS),
                             ("'CustPymt'", PAYMENTS), ("'Estim'", ESTIMATE_HEADERS),
                             ("'SalesOrd'", SALES_ORDERS),
                             ("'PurchOrd'", PURCHASE_ORDERS)):
            if marker in query:
                return iter(rows)
        raise AssertionError(f"the NetSuite fixture has no rows for: {query}")


def build_source() -> netsuite.NetSuiteSource:
    return netsuite.NetSuiteSource(_Client())
