"""Deterministic, offline Zoho source for dev and tests.

Returns raw payloads shaped like the Zoho Books API (contacts / items /
invoices / bills) so the normalizer and sync are exercised against realistic
structures without network or credentials. Data is fixed and small; it is a
stand-in for a real pull, not seed business data.
"""
from __future__ import annotations

from typing import Any, Iterable

_CONTACTS = [
    {"contact_id": "cst-1001", "contact_name": "Pitti Engineering Ltd", "status": "active"},
    {"contact_id": "cst-1002", "contact_name": "Bharat Forge", "status": "active"},
    {"contact_id": "cst-1003", "contact_name": "Kirloskar", "status": "inactive"},
]

_ITEMS = [
    {"item_id": "itm-2001", "name": "CNMG 120408 KCP25", "unit": "pcs",
     "hsn_or_sac": "82090090", "status": "active"},
    {"item_id": "itm-2002", "name": "DNMG 150608 KCK15", "unit": "pcs",
     "hsn_or_sac": "82090090", "status": "active"},
]

_INVOICES = [
    {"invoice_id": "inv-3001", "customer_id": "cst-1001", "date": "2026-06-10",
     "line_items": [
         {"line_item_id": "l1", "item_id": "itm-2001", "quantity": 20, "rate": 530,
          "item_total": 10600},
         {"line_item_id": "l2", "item_id": "itm-2002", "quantity": 10, "rate": 506,
          "item_total": 5060},
     ]},
    {"invoice_id": "inv-3002", "customer_id": "cst-1002", "date": "2026-07-02",
     "line_items": [
         {"line_item_id": "l1", "item_id": "itm-2001", "quantity": 15, "rate": 540},
     ]},
]

_BILLS = [
    {"bill_id": "bill-4001", "date": "2026-05-30",
     "line_items": [
         {"line_item_id": "l1", "item_id": "itm-2001", "quantity": 100, "rate": 405},
     ]},
]


class FixtureZohoSource:
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return list(_CONTACTS)

    def list_items(self) -> Iterable[dict[str, Any]]:
        return list(_ITEMS)

    def list_invoices(self) -> Iterable[dict[str, Any]]:
        return list(_INVOICES)

    def list_bills(self) -> Iterable[dict[str, Any]]:
        return list(_BILLS)
