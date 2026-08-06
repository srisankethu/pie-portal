"""COMMITMENTS — what is promised, in each direction, and not yet in the ledger.

Keyed by party. Three kinds of promise:

- an **open sales order** is what we owe a customer,
- an **open purchase order** is what a supplier owes us,
- an **unpaid bill** is what we owe a supplier in cash.

None of the three is an accounting entry, which is exactly why they were
invisible: the platform read invoices and payments, and a business is exposed
long before either exists.

**A promise is counted from its own document, once.** Supersession means there
is one live event per order, carrying its current status, so a fold over live
events counts each order exactly once and a closed one simply stops qualifying.
Nothing decrements, and nothing needs to.

**Whether a document is still open is read, never inferred.** Zoho states
``invoiced_status``, ``received_status`` and a bill's ``balance``. Deriving
"open" from dates or from what we have not seen invoiced would report an order
as outstanding because our own pull was incomplete.

Deliberately absent:

**No ageing bands, no "at risk", no supplier reliability.** Those need
thresholds with a version, or a prediction. What is stored is the oldest open
promise and the value outstanding; who is late enough to call is a judgement
made from that, with a policy attached.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from ...domain import models
from .. import events as ev
from ..engine import ADD, MIN, SET, Delta, Masters, as_decimal, register

COMMITMENTS = "COMMITMENTS"

#: A sales order Zoho has fully invoiced is no longer a promise to ship
#: something unbilled. Read from Zoho's own field, never inferred.
_INVOICED = {"invoiced"}
#: Likewise for goods: a fully received purchase order has arrived.
_RECEIVED = {"received"}
#: Statuses that never were a commitment. A draft promises nobody anything and
#: a cancelled order promises nobody anything any more.
NOT_A_PROMISE = {"draft", "void", "cancelled", "canceled", "rejected"}


class CommitmentsReducer:
    state = COMMITMENTS
    handles = frozenset({ev.SALES_ORDER_PLACED, ev.PURCHASE_ORDER_PLACED,
                         ev.PAYABLE_RECORDED})

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        payload = event.payload or {}
        if str(payload.get("status") or "").lower() in NOT_A_PROMISE:
            return ()
        if event.event_type == ev.SALES_ORDER_PLACED:
            return self._sales_order(event, payload, ctx)
        if event.event_type == ev.PURCHASE_ORDER_PLACED:
            return self._purchase_order(event, payload, ctx)
        return self._payable(event, payload, ctx, as_of)

    # ── what we owe a customer ──────────────────────────────────────────────
    def _sales_order(self, event: models.BusinessEvent, payload: dict[str, Any],
                     ctx: Masters) -> Iterable[Delta]:
        external_id = str(payload.get("customer_external_id") or "")
        if not external_id:
            # An order with no customer is still an order, but it is not a
            # commitment *to* anybody, and this state is keyed by party. It is
            # counted in the sales-order table, not here.
            return ()
        customer_id = ctx.customer(event, external_id)
        if customer_id is None:
            return ()
        changes: list[tuple[str, str, Any]] = [
            (SET, "direction", "customer"),
            (ADD, "sales_orders", 1),
        ]
        if str(payload.get("invoiced_status") or "").lower() not in _INVOICED:
            total = as_decimal(payload.get("total")) or Decimal(0)
            changes += [
                (ADD, "open_sales_orders", 1),
                (ADD, "open_sales_value", total),
                (MIN, "oldest_open_sale_on", event.occurred_on),
            ]
        return (Delta(COMMITMENTS, customer_id, tuple(changes)),)

    # ── what a supplier owes us, and what we owe them ───────────────────────
    def _purchase_order(self, event: models.BusinessEvent, payload: dict[str, Any],
                        ctx: Masters) -> Iterable[Delta]:
        vendor_id, changes = self._from_supplier(event, payload, ctx,
                                                 "purchase_orders")
        if vendor_id is None:
            return ()
        pending = as_decimal(payload.get("pending_qty"))
        received = str(payload.get("received_status") or "").lower()
        # Open when Zoho says something is still pending, or says it has not
        # been received. A blank pending quantity on an unreceived order is
        # common in this book, and treating that as "arrived" would quietly
        # empty the supply screen.
        still_coming = (pending is not None and pending > 0) or (
            pending is None and received not in _RECEIVED)
        if still_coming:
            total = as_decimal(payload.get("total")) or Decimal(0)
            changes += [
                (ADD, "open_purchase_orders", 1),
                (ADD, "open_purchase_value", total),
                (MIN, "oldest_open_purchase_on", event.occurred_on),
            ]
            if pending is not None:
                changes.append((ADD, "pending_qty", pending))
        return (Delta(COMMITMENTS, vendor_id, tuple(changes)),)

    def _payable(self, event: models.BusinessEvent, payload: dict[str, Any],
                 ctx: Masters, as_of: date) -> Iterable[Delta]:
        vendor_id, changes = self._from_supplier(event, payload, ctx, "bills")
        if vendor_id is None:
            return ()
        balance = as_decimal(payload.get("balance"))
        # ``None`` is "the source did not say", not "settled". Only a balance
        # Zoho actually reported as zero closes a payable.
        if balance is not None and balance > 0:
            changes += [
                (ADD, "unpaid_bills", 1),
                (ADD, "payables_balance", balance),
            ]
            due = payload.get("due_date")
            if due:
                due_on = date.fromisoformat(str(due))
                changes.append((MIN, "earliest_due_on", due_on))
                if due_on < as_of:
                    changes += [
                        (ADD, "overdue_bills", 1),
                        (ADD, "overdue_balance", balance),
                    ]
            # A bill with no terms cannot be aged. Counted as outstanding and
            # named as unageable rather than assumed due — the same choice the
            # ingestion layer makes about the missing date itself.
            else:
                changes.append((ADD, "unageable_bills", 1))
        return (Delta(COMMITMENTS, vendor_id, tuple(changes)),)

    @staticmethod
    def _from_supplier(event: models.BusinessEvent, payload: dict[str, Any],
                       ctx: Masters, counter: str,
                       ) -> tuple[Optional[str], list[tuple[str, str, Any]]]:
        """Resolve the supplier and open the delta both supplier facts share.

        A purchase order and a bill differ in what they count and in nothing
        else up to this point; two copies of the opening would drift the moment
        a third supplier fact is added.
        """
        external_id = str(payload.get("vendor_external_id") or "")
        vendor_id = ctx.vendor(event, external_id) if external_id else None
        if vendor_id is None:
            return None, []
        return vendor_id, [(SET, "direction", "supplier"), (ADD, counter, 1)]


register(CommitmentsReducer())
