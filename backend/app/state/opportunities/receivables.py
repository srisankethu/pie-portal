"""Decisions the RECEIVABLES state can describe.

Two situations about a customer's money, and they are deliberately **not one
situation measured twice**:

**Overdue collection** is money past its date. It is actionable today, it is
sized by exactly the rupees that are late, and the action set is about getting
them in.

**Credit exposure** is a large share of the whole book's receivables sitting
with one customer. It is true even when every one of that customer's invoices
is perfectly current, and the action set is about terms rather than chasing.
Sizing it on the overdue portion would make it the first card again; sizing it
on the customer's whole balance, and gating it on *share of the book*, is what
makes it a different claim.

They can both fire on one customer, and that is correct rather than a
double-count: a large customer who is also late is genuinely two conversations —
"collect this" and "should we be this exposed at all". The card's ``basis``
sentence says which rupees each one is talking about, so a reader is never
invited to add them.

What is deliberately not here:

**No collection probability, no expected recovery value, no bad-debt
provision.** All three are predictions. This book records no write-offs to
calibrate against, so any figure would be a guess wearing a decimal point —
and a guess presented beside deterministic rupees is the one that gets
believed.

**No days-sales-outstanding and no ageing buckets.** DSO needs a revenue window
this state does not hold, and buckets need band edges with a version. What is
computable is the balance, the overdue portion, and how far past its date the
oldest overdue invoice is — which is what a person actually chases against.

**No "customer is a credit risk".** That is a judgement about a relationship,
built from things the platform cannot see: what they said on the phone, what
their order book looks like, whether their own customer paid them. The platform
states the exposure and stops.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from ...domain.enums import DecisionType, SubjectEntityType
from ..reducers.receivables import RECEIVABLES
from .base import (AGREE_PAYMENT_PLAN, CHASE_PAYMENT, HOLD_FURTHER_SUPPLY,
                   SEND_STATEMENT, TIGHTEN_CREDIT_TERMS, DecisionPolicy, Impact,
                   OpportunityDraft, day, money, number, register)

_CUSTOMER = SubjectEntityType.CUSTOMER.value


def _customers(states: dict[str, dict[str, dict[str, Any]]],
               ) -> Iterable[tuple[str, dict[str, Any]]]:
    """Every party the receivables fold recorded as a customer.

    Direction is read from the state rather than inferred from which fields are
    populated — the same rule the supplier detectors follow, and for the same
    reason: a party can be both, and guessing from the shape of the row would
    file one side's card against the other.
    """
    for key, value in (states.get(RECEIVABLES) or {}).items():
        if value.get("direction") == "customer":
            yield key, value


class OverdueReceivableDetector:
    """Invoices past their due date, by customer.

    A collection decision. It says what is owed and how overdue, and stops short
    of saying whether it will be recovered — that needs a default history this
    book does not have.

    Invoices with no terms are counted and named separately. They cannot be
    aged, and assuming a due date would put a customer on this list for an
    obligation nobody ever gave them.
    """

    decision_type = DecisionType.CASH_RECEIVABLE_OVERDUE.value
    states = frozenset({RECEIVABLES})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for customer_id, value in _customers(states):
            overdue = number(value, "overdue_balance") or Decimal(0)
            if overdue < policy.min_impact:
                continue
            # The oldest invoice actually past its date. `earliest_due_on` would
            # be a future date for a customer whose only unpaid invoice is not
            # due yet, and reporting that as "days past due" would be negative.
            due = day(value, "oldest_overdue_due_on")
            days_past = (as_of - due).days if due and due < as_of else None
            unageable = value.get("unageable_invoices") or 0
            last_paid = day(value, "last_paid_on")
            since_paid = (as_of - last_paid).days if last_paid else None
            overdue_count = value.get("overdue_invoices") or 0
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_CUSTOMER,
                subject_entity_id=customer_id,
                impact=Impact(
                    financial=money(overdue),
                    basis="Invoice balances past their due date with this customer",
                    operational={
                        "overdue_invoices": overdue_count,
                        "open_invoices": value.get("open_invoices") or 0,
                        "oldest_overdue_due_on": due.isoformat() if due else None,
                        "days_past_due": days_past,
                        "unageable_invoices": unageable,
                        "outstanding": str(number(value, "outstanding") or Decimal(0)),
                        "last_paid_on": last_paid.isoformat() if last_paid else None,
                        "days_since_last_receipt": since_paid,
                    }),
                rationale=(
                    f"{overdue_count} invoice(s) past due with this customer"
                    + (f", the oldest by {days_past} days" if days_past else "")
                    + (f". Last payment received {since_paid} days ago."
                       if since_paid is not None else
                       ". No payment from this customer has been read at all.")
                    + (f" A further {unageable} invoice(s) carry no payment "
                       "terms and cannot be aged." if unageable else "")),
                evidence={
                    "state": RECEIVABLES, "key": customer_id,
                    "overdue_balance": str(overdue),
                    "overdue_invoices": overdue_count,
                    "oldest_overdue_due_on": due.isoformat() if due else None,
                    "unageable_invoices": unageable,
                    "last_paid_on": last_paid.isoformat() if last_paid else None,
                },
                actions=(CHASE_PAYMENT, SEND_STATEMENT, AGREE_PAYMENT_PLAN,
                         HOLD_FURTHER_SUPPLY),
                state_keys=(customer_id,))


class CreditExposureDetector:
    """One customer holding a large share of everything that is owed.

    Not a lateness finding. This fires on a customer whose invoices are all
    current, because the exposure is real either way: if they stop paying, that
    share of the book stops with them. It is the receivables twin of the
    storyboard's revenue-concentration beat, and it is stated the same way —
    as a standing position, not as a change.

    The share is measured against the *outstanding* book rather than against
    revenue. Revenue concentration is a commercial fact about who buys; this is
    a cash fact about whose money we are waiting on, and the two can differ
    sharply when one large customer pays promptly and another does not.
    """

    decision_type = DecisionType.CASH_CREDIT_EXPOSURE.value
    states = frozenset({RECEIVABLES})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        rows = list(_customers(states))
        book = sum((number(v, "outstanding") or Decimal(0) for _, v in rows),
                   Decimal(0))
        # With nothing outstanding there is no book to be a share of. Guarding
        # rather than dividing: a zero here is a legitimate state (everything
        # collected), not an error, and it must produce no card rather than an
        # exception.
        if book <= 0:
            return
        for customer_id, value in rows:
            outstanding = number(value, "outstanding") or Decimal(0)
            if outstanding < policy.min_impact:
                continue
            share = outstanding / book
            if share < policy.exposure_share:
                continue
            open_invoices = value.get("open_invoices") or 0
            overdue = number(value, "overdue_balance") or Decimal(0)
            last_paid = day(value, "last_paid_on")
            pct = (share * 100).quantize(Decimal("0.1"))
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_CUSTOMER,
                subject_entity_id=customer_id,
                impact=Impact(
                    financial=money(outstanding),
                    # Says which rupees this is. The overdue card above may name
                    # a subset of the same balance, and without this sentence a
                    # reader would be invited to add the two figures.
                    basis=("Everything this customer currently owes, due and "
                           "not yet due"),
                    operational={
                        "share_of_receivables_pct": str(pct),
                        "open_invoices": open_invoices,
                        "overdue_balance": str(overdue),
                        "receivables_book": str(money(book)),
                        "last_paid_on": last_paid.isoformat() if last_paid else None,
                    }),
                rationale=(
                    f"This customer holds {pct}% of everything currently owed "
                    f"to the business, across {open_invoices} open invoice(s). "
                    "That is a standing exposure rather than a change: if this "
                    "one relationship stops paying, that share of the book "
                    "stops with it."
                    + (f" {money(overdue)} of it is already past due."
                       if overdue > 0 else
                       " None of it is past due yet.")),
                evidence={
                    "state": RECEIVABLES, "key": customer_id,
                    "outstanding": str(outstanding),
                    "receivables_book": str(money(book)),
                    "share_of_receivables": str(share.quantize(Decimal("0.0001"))),
                    "open_invoices": open_invoices,
                    "exposure_share_threshold": str(policy.exposure_share),
                },
                actions=(TIGHTEN_CREDIT_TERMS, AGREE_PAYMENT_PLAN,
                         HOLD_FURTHER_SUPPLY, SEND_STATEMENT),
                state_keys=(customer_id,))


register(OverdueReceivableDetector())
register(CreditExposureDetector())
