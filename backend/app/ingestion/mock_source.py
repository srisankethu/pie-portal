"""Deterministic, offline Zoho source for dev and tests.

Returns raw payloads shaped like the Zoho Books API (contacts / items /
invoices / bills / vendors / payments / purchase orders / quotes) so the
normalizer and sync are exercised against realistic structures without network
or credentials. Data is fixed and small; it is a
stand-in for a real pull, not seed business data.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .source import SkipPredicate

_CONTACTS = [
    {"contact_id": "cst-1001", "contact_name": "Pitti Engineering Ltd",
     "gst_no": "36AAACP1234C1ZP", "status": "active"},
    {"contact_id": "cst-1002", "contact_name": "Bharat Forge",
     "gst_no": "27AAACB5678D1ZQ", "status": "active"},
    {"contact_id": "cst-1003", "contact_name": "Kirloskar", "status": "inactive"},
]

_ITEMS = [
    # Stock mirrors what the live API actually returns, including the two
    # shapes that matter: a blank reorder_level (the common case — a reorder
    # point nobody has set), and an actual_available below zero (more committed
    # to open orders than exists on the shelf).
    {"item_id": "itm-2001", "name": "CNMG 120408 KCP25", "sku": "CNMG120408KCP25", "unit": "pcs",
     "hsn_or_sac": "82090090", "status": "active",
     "stock_on_hand": 40, "available_stock": 40, "actual_available_stock": -5,
     "reorder_level": "", "purchase_rate": 405, "track_inventory": True,
     "item_type": "inventory"},
    {"item_id": "itm-2002", "name": "DNMG 150608 KCK15", "sku": "DNMG150608KCK15", "unit": "pcs",
     "hsn_or_sac": "82090090", "status": "active",
     "stock_on_hand": 0, "available_stock": 0, "actual_available_stock": 0,
     "reorder_level": 10, "purchase_rate": 480, "track_inventory": True,
     "item_type": "inventory"},
]

_VENDORS = [
    {"contact_id": "vnd-5001", "contact_name": "Kennametal India Ltd",
     "gst_no": "29AAACW1234K1ZR", "pan_no": "AAACW1234K", "payment_terms": 30,
     "status": "active"},
    {"contact_id": "vnd-5002", "contact_name": "YG Cutting Tools",
     "gst_no": "36AAACY5678M1ZT", "pan_no": "AAACY5678M", "payment_terms": 0,
     "status": "active"},
]

_PAYMENTS = [
    # Paid 31 days after the invoice, 1 day after it fell due.
    {"payment_id": "pay-6001", "customer_id": "cst-1001", "date": "2026-07-11",
     "amount": 15660, "payment_mode": "Bank Transfer", "is_advance_payment": False,
     "unused_amount": 0,
     "invoices": [
         {"invoice_payment_id": "pa-1", "invoice_id": "inv-3001",
          "invoice_number": "INV-3001", "date": "2026-06-10",
          "due_date": "2026-07-10", "amount_applied": 15660},
     ]},
    # An advance: money with no invoice behind it, so no days-to-pay exists.
    {"payment_id": "pay-6002", "customer_id": "cst-1002", "date": "2026-07-05",
     "amount": 50000, "payment_mode": "Bank Transfer", "is_advance_payment": True,
     "unused_amount": 50000, "invoices": []},
]

_PURCHASE_ORDERS = [
    # Still open, and with no promised date — the common shape in this book.
    {"purchaseorder_id": "po-7001", "purchaseorder_number": "PO/26/001",
     "vendor_id": "vnd-5001", "date": "2026-05-20", "expected_delivery_date": "",
     "status": "open", "received_status": "to_be_received",
     "total_ordered_quantity": 75, "quantity_yet_to_receive": 75,
     "total": 58690.13, "receives": []},
    {"purchaseorder_id": "po-7002", "purchaseorder_number": "PO/26/002",
     "vendor_id": "vnd-5002", "date": "2026-06-01", "expected_delivery_date": "",
     "status": "billed", "received_status": "received",
     "total_ordered_quantity": 10, "quantity_yet_to_receive": 0,
     "total": 3999.91,
     "receives": [{"date": "2026-06-15", "receive_id": "rcv-1"}]},
]

_QUOTES = [
    # Zoho's own noun for the document is "estimate" and these payloads are
    # shaped as its API returns them; the platform's noun is "quote" from the
    # normalizer onward.
    #
    # Three rows and three outcomes on purpose, because the third is the one
    # this pull exists for. Accepted and declined are the two the ERP can
    # prove; `expired` is a quote that lapsed, which is not a loss — it spans
    # "nobody chased it", "the customer never answered" and "we lost it", and
    # a fixture that omitted the case would let a reader believe every quote
    # resolves to a win or a loss.
    {"estimate_id": "est-8001", "estimate_number": "SLS/QTN-201",
     "reference_number": "RFQ/PITTI/88", "customer_id": "cst-1001",
     "customer_name": "Pitti Engineering Ltd", "date": "2026-05-02",
     "expiry_date": "2026-05-31", "status": "invoiced",
     "accepted_date": "2026-05-09", "declined_date": "",
     "total": 15660, "salesperson_id": "zu-1",
     "client_viewed_time": "2026-05-03T10:15:00+0530",
     "cf_quote_type": "REPEAT",
     # What was on it. Carried inline because this source has no detail call to
     # make — the demo has to be able to show a quote's lines, or the one screen
     # that reads them cannot be looked at without a live ERP.
     "line_items": [
         {"line_item_id": "eli-1", "item_id": "itm-1", "sku": "CNMG120408-MP",
          "description": "CNMG 120408 MP KCP25 turning insert",
          "quantity": 30, "unit": "pcs", "rate": 452, "item_total": 13560},
         {"line_item_id": "eli-2", "sku": "", "name": "Freight",
          "description": "Freight and handling",
          "quantity": 1, "unit": "nos", "rate": 2100, "item_total": 2100},
     ]},
    {"estimate_id": "est-8002", "estimate_number": "SLS/QTN-202",
     "reference_number": "", "customer_id": "cst-1002",
     "customer_name": "Bharat Forge", "date": "2026-05-14",
     "expiry_date": "2026-06-13", "status": "declined",
     "accepted_date": "", "declined_date": "2026-05-28",
     "total": 42000, "salesperson_id": "zu-1",
     "client_viewed_time": "2026-05-15T09:00:00+0530",
     "cf_quote_type": "NEW",
     "line_items": [
         {"line_item_id": "eli-3", "item_id": "itm-2", "sku": "WNMG080408",
          "description": "WNMG 080408 roughing insert",
          "quantity": 100, "unit": "pcs", "rate": 420, "item_total": 42000},
     ]},
    # Lapsed, and never opened by the customer — the shape most of a real book
    # sits in, and the one nothing may read as a loss.
    {"estimate_id": "est-8003", "estimate_number": "SLS/QTN-203",
     "reference_number": "", "customer_id": "cst-1003",
     "customer_name": "Kirloskar", "date": "2026-04-06",
     "expiry_date": "2026-05-06", "status": "expired",
     "accepted_date": "", "declined_date": "",
     "total": 8750, "salesperson_id": "zu-1",
     "client_viewed_time": ""},
]

_USERS = [
    {"user_id": "zu-1", "email": "r.nair@pie.example", "name": "R. Nair", "status": "active"},
]

_INVOICES = [
    {"invoice_id": "inv-3001", "customer_id": "cst-1001", "date": "2026-06-10",
     "salesperson_id": "zu-1", "salesperson_name": "R. Nair",
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
    """The offline source has no per-document call to save, so it honours
    ``skip`` only in the sense of accepting it — the fixture is returned whole."""

    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return list(_CONTACTS)

    def list_items(self) -> Iterable[dict[str, Any]]:
        return list(_ITEMS)

    def list_invoices(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        return list(_INVOICES)

    def list_bills(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        return list(_BILLS)

    def list_users(self) -> Iterable[dict[str, Any]]:
        return list(_USERS)

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        return list(_VENDORS)

    def list_customer_payments(
            self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        return list(_PAYMENTS)

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        return list(_PURCHASE_ORDERS)

    def list_quotes(self, skip: Any = None) -> Iterable[dict[str, Any]]:
        """``skip`` accepted and ignored, like this class's other pulls.

        The demo payloads carry their ``line_items`` inline, so there is no
        detail call to save — but the *signature* has to match the protocol or
        the sync's one call site raises ``TypeError`` and takes the whole quote
        stage down. That is not hypothetical: the real client grew ``skip`` and
        this one did not, and nothing failed until a demo org ran a sync.

        After the fixtures, whatever the mock *writer* has sent from the Quote
        Builder — as a live book would list an estimate this platform created
        in it. Without this a quote sent in the demo never gained an ERP side,
        so the one join the outcome of record turns on could not be seen
        without a live ERP.
        """
        return list(_QUOTES) + [_as_estimate(w) for w in _written()]


def _written() -> list[dict[str, Any]]:
    # Imported here, not at the top: ``app.zoho`` imports this package's
    # ``erp.base`` and ``errors``, and a module-level import would close the
    # cycle at load.
    from ..zoho import mock_zoho
    return mock_zoho.written_documents()


def _as_estimate(w: dict[str, Any]) -> dict[str, Any]:
    """One written document in the estimate shape ``normalize_quote_document``
    reads — the same keys the fixtures above carry. No contact id: the mock
    has no ledger for one, and the sync keeps a quote whose customer it
    cannot resolve. A total only where every line has a quantity and a rate;
    a partial sum would be a false total, and ``None`` is "not stated"."""
    est, lines = w["document"], w["lines"]
    day = w["written_on"].isoformat()
    priced = all(ln.get("qty") is not None and ln.get("rate") is not None for ln in lines)
    total = (round(sum(float(ln["qty"]) * float(ln["rate"]) for ln in lines), 2)
             if lines and priced else None)
    return {
        "estimate_id": est.document_id, "estimate_number": est.number,
        "reference_number": w["reference"], "customer_id": "",
        "customer_name": w["customer"], "date": day, "expiry_date": "",
        "status": "sent", "accepted_date": "", "declined_date": "",
        "total": total, "created_time": f"{day}T00:00:00+0530",
        "line_items": [
            {"line_item_id": f"{est.document_id}:{i}", "item_id": ln.get("itemId") or "",
             "sku": ln.get("code") or "", "description": ln.get("code") or "",
             "quantity": ln.get("qty"), "unit": "", "rate": ln.get("rate"),
             "item_total": (round(float(ln["qty"]) * float(ln["rate"]), 2)
                            if ln.get("qty") is not None and ln.get("rate") is not None
                            else None)}
            for i, ln in enumerate(lines)],
    }
