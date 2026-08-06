"""RECEIVABLES — what customers owe us, and how overdue.

Keyed by customer. The mirror of the payables half of ``COMMITMENTS``, and
deliberately the same shape: one is what we owe a supplier in cash, the other
what a customer owes us. Two different shapes for one question — "what is
outstanding, and how late" — would mean two ways to ask it and two places for
the ageing rule to drift.

This is the state the Decision Intelligence analysis named as missing:
``PaymentReceipt`` had recorded money *in* since the cash screen was built, but
receipts are the half that already arrived. Nothing folded what had not.

**Balance is read, never derived.** Zoho states what is still owed on an
invoice. Computing it as ``total`` minus the receipts we happen to have read
would be wrong the moment a credit note is applied — and wrong in the direction
that overstates what is collectable, which is the direction that gets a
customer chased for money they do not owe.

**An invoice with no due date is counted, not aged.** Terms are blank on some
invoices in this book. Defaulting the due date to the invoice date would report
every untermed invoice as overdue from the day it was raised and put the
customer on a collections list for an obligation nobody gave them. They are
carried as ``unageable_invoices`` so the total still reconciles and the gap is
visible rather than silently absorbed.

**Receipts are folded for one fact only: when money last arrived.** Not for the
balance — the balance already nets them, and adding receipts to it would
double-count every payment. "When did we last hear from them, in cash" is the
one thing a balance genuinely cannot answer, and it is what separates a
customer who is slow from one who has stopped.

Deliberately absent:

**No ageing buckets, no "days sales outstanding", no credit limit.** Buckets
need band edges with a version; DSO needs a revenue window this state does not
hold; a credit limit is a commercial term Zoho does not expose on the contact
in this book. What is stored is the balance, the earliest due date and the
overdue portion — who is late enough to chase is a judgement made from that,
with a policy attached.

**No collection probability and no expected recovery.** Both are predictions.
The platform has payment history and no defaults to calibrate against, so any
figure would be a guess wearing a decimal point.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from ...domain import models
from .. import events as ev
from ..engine import ADD, MAX, MIN, SET, Delta, Masters, as_decimal, register

RECEIVABLES = "RECEIVABLES"

#: Statuses that never were a receivable. A draft invoices nobody, and a void
#: or written-off invoice is not money anyone is going to collect.
_NOT_OWED = {"draft", "void", "cancelled", "canceled", "written_off"}


class ReceivablesReducer:
    state = RECEIVABLES
    handles = frozenset({ev.RECEIVABLE_RECORDED, ev.PAYMENT_RECEIVED})

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        payload = event.payload or {}
        if event.event_type == ev.PAYMENT_RECEIVED:
            return self._receipt(event, payload, ctx)
        if str(payload.get("status") or "").lower() in _NOT_OWED:
            return ()
        return self._receivable(event, payload, ctx, as_of)

    # ── what a customer owes ────────────────────────────────────────────────
    def _receivable(self, event: models.BusinessEvent, payload: dict[str, Any],
                    ctx: Masters, as_of: date) -> Iterable[Delta]:
        customer_id = self._customer(event, payload, ctx)
        if customer_id is None:
            return ()
        changes: list[tuple[str, str, Any]] = [
            (SET, "direction", "customer"),
            (ADD, "invoices", 1),
        ]
        total = as_decimal(payload.get("total"))
        if total is not None:
            changes.append((ADD, "invoiced_value", total))

        balance = as_decimal(payload.get("balance"))
        # ``None`` is "the source did not say", not "collected". Only a balance
        # Zoho actually reported as greater than zero is outstanding. A negative
        # balance is an over-payment or an applied credit note and is not
        # something to collect, so it does not open a receivable either.
        if balance is not None and balance > 0:
            changes += [
                (ADD, "open_invoices", 1),
                (ADD, "outstanding", balance),
                (MIN, "oldest_open_on", event.occurred_on),
            ]
            due = payload.get("due_date")
            if due:
                due_on = date.fromisoformat(str(due))
                changes.append((MIN, "earliest_due_on", due_on))
                if due_on < as_of:
                    changes += [
                        (ADD, "overdue_invoices", 1),
                        (ADD, "overdue_balance", balance),
                        # The oldest thing actually past its date, which is what
                        # decides how bad this is. `earliest_due_on` alone would
                        # be a future date for a customer whose only unpaid
                        # invoice is not due yet.
                        (MIN, "oldest_overdue_due_on", due_on),
                    ]
            else:
                changes.append((ADD, "unageable_invoices", 1))
        return (Delta(RECEIVABLES, customer_id, tuple(changes)),)

    # ── when money last arrived ─────────────────────────────────────────────
    def _receipt(self, event: models.BusinessEvent, payload: dict[str, Any],
                 ctx: Masters) -> Iterable[Delta]:
        """One fact, and only one: the date.

        Not the amount. The outstanding balance above already nets every receipt
        Zoho has applied, so adding receipts to this state would count each
        payment twice — once as the reduction it caused and once as itself.
        """
        customer_id = self._customer(event, payload, ctx)
        if customer_id is None:
            return ()
        return (Delta(RECEIVABLES, customer_id, (
            (SET, "direction", "customer"),
            (MAX, "last_paid_on", event.occurred_on),
            (ADD, "receipts", 1),
        )),)

    @staticmethod
    def _customer(event: models.BusinessEvent, payload: dict[str, Any],
                  ctx: Masters) -> Optional[str]:
        external_id = str(payload.get("customer_external_id") or "")
        if not external_id:
            # Money owed by nobody in particular is still owed, but this state
            # is keyed by party and has nowhere to put it. It stays in the
            # invoice table, where the total still reconciles.
            return None
        return ctx.customer(event, external_id)


register(ReceivablesReducer())
