"""Decisions the COMMITMENTS state can describe.

Two situations, both about a supplier: what we have committed to buy and not
received, and what we owe and have not paid.

What is deliberately not here, and why it matters more than what is:

**No "supplier delay" and no "late delivery".** Both need a promised date, and
``expected_delivery_date`` is blank on effectively every order in this book —
the ingestion layer records that rather than substituting an assumed lead time,
and this layer will not undo that by calling *age* lateness. An order open for
90 days is reported as 90 days old, which is a fact, and never as 30 days late,
which would be an invention.

**No supplier concentration or dependency — they live next door now.** Both
need spend joined to a vendor, which neither of this module's inputs has:
``INVENTORY.spend`` is per product and ``COMMITMENTS`` holds open promises
rather than historical spend. The SUPPLIER reducer this file used to say did
not exist now does, and ``opportunities/supplier`` reads it. Kept as a pointer
rather than deleted, because "we cannot do that" outliving the reason is how a
reader stops asking for something the platform can do.

**No cash pressure or liquidity risk.** Those need a bank balance, and PIE
reads payments rather than balances. Payables are one side of a ledger, and one
side of a ledger is not cash — the same reason vendor payments were added in
the first place. What *is* computable is which payables are overdue and by how
much, which is a payment-prioritisation decision and is scoped as one.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from ...domain.enums import DecisionType, SubjectEntityType
from ..reducers.commitments import COMMITMENTS
from .base import (CANCEL_PURCHASE_ORDER, CHASE_SUPPLIER, DEFER_PURCHASE,
                   NEGOTIATE_TERMS, PAY_NOW, SPLIT_PURCHASE, DecisionPolicy,
                   Impact, OpportunityDraft, day, money, number, register)

#: A supplier is its own kind of subject. Borrowing CUSTOMER would file the
#: card under the wrong party and resolve to the wrong name, so ``VENDOR`` was
#: added to the vocabulary rather than approximated with an existing member.
_SUPPLIER = SubjectEntityType.VENDOR.value


def _suppliers(states: dict[str, dict[str, dict[str, Any]]],
               ) -> Iterable[tuple[str, dict[str, Any]]]:
    """Every party the commitments fold recorded as a supplier.

    Direction is read from the state rather than inferred from which fields are
    populated: a party can be both a customer and a supplier, and guessing from
    the shape of the row would put a customer's card in a supplier's queue.
    """
    for key, value in (states.get(COMMITMENTS) or {}).items():
        if value.get("direction") == "supplier":
            yield key, value


class OpenCommitmentDetector:
    """Money committed to a supplier that has not arrived.

    Sized on the value of open purchase orders, because that is what the
    business is exposed to: the cash is promised and the stock is not on the
    shelf. Age is reported alongside — not as lateness, but because an order
    open for six months is a different conversation from one open for six days.
    """

    decision_type = DecisionType.SUP_OPEN_COMMITMENT.value
    states = frozenset({COMMITMENTS})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for vendor_id, value in _suppliers(states):
            open_orders = value.get("open_purchase_orders") or 0
            committed = number(value, "open_purchase_value") or Decimal(0)
            if not open_orders or committed < policy.min_impact:
                continue
            oldest = day(value, "oldest_open_purchase_on")
            age = (as_of - oldest).days if oldest else None
            pending = number(value, "pending_qty")
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_SUPPLIER,
                subject_entity_id=vendor_id,
                impact=Impact(
                    financial=money(committed),
                    basis=("Value of purchase orders placed with this supplier "
                           "and not yet received"),
                    operational={
                        "open_orders": open_orders,
                        "oldest_open_on": oldest.isoformat() if oldest else None,
                        "oldest_age_days": age,
                        "pending_qty": (str(pending) if pending is not None else None),
                    }),
                rationale=(
                    f"{open_orders} order(s) placed with this supplier and "
                    "still unreceived"
                    + (f", the oldest open for {age} days" if age is not None else "")
                    + ". This book records no promised delivery dates, so the "
                      "age is reported rather than measured against a promise."),
                evidence={
                    "state": COMMITMENTS, "key": vendor_id,
                    "open_purchase_orders": open_orders,
                    "open_purchase_value": str(committed),
                    "oldest_open_purchase_on": oldest.isoformat() if oldest else None,
                    "pending_qty": str(pending) if pending is not None else None,
                },
                actions=(CHASE_SUPPLIER, SPLIT_PURCHASE, DEFER_PURCHASE,
                         CANCEL_PURCHASE_ORDER),
                state_keys=(vendor_id,))


class OverduePayableDetector:
    """Bills past their due date, by supplier.

    A payment-prioritisation decision, not a liquidity one: it says what is
    owed and how overdue, and stops short of saying whether it can be paid,
    because that needs a bank balance the platform does not read.

    Bills with no terms are counted and named separately. They cannot be aged,
    and assuming a due date would report every untermed bill as overdue from
    the day it was raised.
    """

    decision_type = DecisionType.CASH_PAYABLE_OVERDUE.value
    states = frozenset({COMMITMENTS})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for vendor_id, value in _suppliers(states):
            overdue = number(value, "overdue_balance") or Decimal(0)
            if overdue < policy.min_impact:
                continue
            due = day(value, "earliest_due_on")
            days_past = (as_of - due).days if due and due < as_of else None
            unageable = value.get("unageable_bills") or 0
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_SUPPLIER,
                subject_entity_id=vendor_id,
                impact=Impact(
                    financial=money(overdue),
                    basis="Bill balances past their due date with this supplier",
                    operational={
                        "overdue_bills": value.get("overdue_bills") or 0,
                        "unpaid_bills": value.get("unpaid_bills") or 0,
                        "earliest_due_on": due.isoformat() if due else None,
                        "days_past_due": days_past,
                        "unageable_bills": unageable,
                        "payables_balance": str(
                            number(value, "payables_balance") or Decimal(0)),
                    }),
                rationale=(
                    f"{value.get('overdue_bills') or 0} bill(s) past due with "
                    "this supplier"
                    + (f", the earliest by {days_past} days" if days_past else "")
                    + (f". A further {unageable} bill(s) carry no payment terms "
                       "and cannot be aged." if unageable else ".")),
                evidence={
                    "state": COMMITMENTS, "key": vendor_id,
                    "overdue_balance": str(overdue),
                    "overdue_bills": value.get("overdue_bills") or 0,
                    "earliest_due_on": due.isoformat() if due else None,
                    "unageable_bills": unageable,
                },
                actions=(PAY_NOW, NEGOTIATE_TERMS),
                state_keys=(vendor_id,))


register(OpenCommitmentDetector())
register(OverduePayableDetector())
