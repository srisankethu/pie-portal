"""How long an order takes to become cash, and which half of the wait is ours.

Three dates and two gaps: the customer orders, we invoice, they pay. The
platform could already measure the second gap — ``insight/payments`` has done it
per settled invoice since receipts were ingested — and could not measure the
first at all, because the invoice header carried no order reference. So "we are
slow to collect" and "we are slow to *bill*" were the same number, and only one
of them is the customer's fault.

    order date ──[ ours ]──▶ invoice date ──[ theirs ]──▶ payment received

**The measurement rule, stated because it is a choice and not the only one.**

*Grain: the invoice.* Cash arrives against an invoice, so an invoice is one
observation of the whole cycle. That decides both of the awkward cases below,
and it is why this module reads invoices and joins orders onto them rather than
the other way round.

*Stage 1 (order → invoice) counts from the* **earliest** *order the invoice
bills against.* On a consolidated invoice the oldest order is the one that
waited longest, and that wait is the thing worth seeing. Zoho's scalar
``salesorder_id`` — its own "primary" — is deliberately **not** what selects the
date: which order Zoho names first is an arbitrary tie-break inside the payload,
and letting it choose would make a measurement depend on it. The scalar is
persisted (``InvoiceSalesOrderLink.is_primary``) so nothing is lost; it is just
not consulted here.

*Stage 2 (invoice → payment) is not recomputed.* It is
``payments.Settlement.days_to_pay`` of the last application against the invoice,
read as-is. A second subtraction of the same two dates in this module is exactly
the drift ``CLAUDE.md`` §2 is about, and the payment screen and this one must
never disagree about how long one invoice took to be paid.

*Stage 3 is measured, not added.* The total is each invoice's own order date to
its own payment date. Reporting ``median(stage 1) + median(stage 2)`` would be a
figure no invoice ever had, in the same way the mean of per-line margins is not
the book's margin.

**What this rule drops, said plainly.** The relationship is many-to-many and
both directions occur in this book:

* *One order, several invoices.* Partial invoicing is normal here — one order
  appears on two invoices — and each invoice becomes its own observation. So a
  partially-invoiced order contributes several stage-1 figures rather than one.
  That is right for "how long until the cash came" and would be wrong for "how
  long until the order was fully invoiced", which is a different question this
  module does not answer.
* *One invoice, several orders.* The invoice reports **one** lag, measured from
  the earliest of them — the worst case among the orders it covers. The other
  orders' individual lags are not reported. ``consolidated`` counts these
  invoices so a reader can see how much of the book is being read that way, and
  the screen says which rule produced the number.

**An invoice with no order has no order-to-invoice lag.** Stock sold across the
counter never had one, and that is UNKNOWN, never zero — a zero would be the
fastest possible conversion and would drag the median toward "we bill
instantly". The same applies at every other point evidence runs out, and each
one is a *named* reason rather than a shared blank: an invoice whose orders are
outside the synced window is not the same fact as an invoice with no order, and
an invoice cleared by a credit note is not an invoice paid in zero days.

Dates and day counts only. Nothing here reads a cost, a price or a margin, so
the whole surface is safe for a salesperson — see ``routers/insight``.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from . import payments

#: Fewer observations than this and a "typical cycle time" is one invoice
#: wearing a suit. Deliberately ``payments.MIN_SETTLEMENTS`` rather than a
#: second number of its own: this screen and the payment screen describe the
#: same invoices, and one going estimable at three beside the other going
#: estimable at five would put two different evidence bars on one page.
MIN_OBSERVATIONS = payments.MIN_SETTLEMENTS

#: The measurement rule, as the screen has to state it. A cycle time whose
#: reader cannot see which of several defensible rules produced it is a number
#: that gets argued about rather than acted on.
RULE: dict[str, str] = {
    "code": "EARLIEST_LINKED_ORDER",
    "grain": "invoice",
    "statement": (
        "One row per invoice. The order-to-invoice gap is measured from the "
        "earliest order the invoice bills against; the invoice-to-payment gap "
        "is the invoice's own days-to-pay, as the payments view computes it."),
    "drops": (
        "An invoice covering several orders reports one gap — the longest of "
        "them — not one per order. An order invoiced in parts appears once per "
        "invoice, because the question is when the cash arrived."),
}

#: Who a stage's delay belongs to. The point of the whole screen: a slow cycle
#: is a different job of work depending on which of these two halves it sits in.
OWNERS: dict[str, str] = {
    "US": "Ours",
    "CUSTOMER": "The customer's",
    "BOTH": "Both",
}

#: Why one stage is unknown for one invoice. Named rather than blank, because
#: these are four different facts and only one of them is a gap in the sync.
UNKNOWN_REASONS: dict[str, str] = {
    "NO_ORDER": (
        "This invoice names no sales order. Stock sold across the counter never "
        "had one, so there is no order date to measure from — not a delay of "
        "zero."),
    "ORDER_NOT_HELD": (
        "The invoice names an order this platform does not hold, so its date is "
        "unknown. Usually an order raised before the synced window opens."),
    "STILL_OWED": (
        "Not settled yet, so the clock is still running. Reported as unknown "
        "rather than as the days elapsed so far, which would look like a "
        "completed cycle."),
    "NO_RECEIPT_ON_RECORD": (
        "Nothing is owed, but no payment is recorded against it — usually "
        "cleared by a credit note. Cleared is not paid, and it carries no "
        "payment date."),
    "BALANCE_UNKNOWN": (
        "The source states no balance for this invoice, so whether it is "
        "settled cannot be answered. Never read as collected."),
}


@dataclass(frozen=True)
class Stage:
    """One leg of the cycle, as the words that go with it."""

    key: str
    label: str
    #: A key of ``OWNERS``.
    owner: str
    question: str


STAGES: tuple[Stage, ...] = (
    Stage(key="ORDER_TO_INVOICE", label="Order to invoice", owner="US",
          question="How long we take to turn a customer's order into an invoice."),
    Stage(key="INVOICE_TO_CASH", label="Invoice to payment", owner="CUSTOMER",
          question="How long the customer takes to settle once invoiced."),
    Stage(key="ORDER_TO_CASH", label="Order to cash", owner="BOTH",
          question="The whole cycle, measured end to end on the same invoices."),
)


@dataclass(frozen=True)
class Cycle:
    """One invoice, with the three dates the cycle runs between.

    Assembled by the caller from persisted rows. Nothing here reaches for the
    database, and nothing here computes ``days_to_pay`` — it arrives already
    computed by ``payments.Settlement``, which is the one owner of that
    subtraction.
    """

    invoice_ref: str
    invoice_number: Optional[str]
    customer_id: Optional[str]
    customer_label: str
    invoice_date: date
    #: The earliest date among the orders this invoice bills against, when any
    #: of them is held. ``None`` covers both "no order named" and "orders named,
    #: none of them held" — which ``order_reason`` tells apart.
    order_date: Optional[date]
    #: How many orders the invoice names, held or not.
    orders_linked: int
    #: The numbers as the invoice stated them, for the reader.
    order_numbers: tuple[str, ...]
    #: ``payments.Settlement.days_to_pay`` of the last application against this
    #: invoice. ``None`` when nothing has been applied to it.
    days_to_pay: Optional[int]
    #: Whether anything is still owed. ``None`` when the source states no
    #: balance — a third answer, never folded into "settled".
    outstanding: Optional[bool]

    @property
    def order_reason(self) -> Optional[str]:
        """Why stage 1 is unknown, or ``None`` when it is known."""
        if self.order_date is not None:
            return None
        return "ORDER_NOT_HELD" if self.orders_linked else "NO_ORDER"

    @property
    def cash_reason(self) -> Optional[str]:
        """Why stage 2 is unknown, or ``None`` when it is known."""
        if self.outstanding is None:
            return "BALANCE_UNKNOWN"
        if self.outstanding:
            return "STILL_OWED"
        return None if self.days_to_pay is not None else "NO_RECEIPT_ON_RECORD"

    @property
    def order_to_invoice_days(self) -> Optional[int]:
        if self.order_date is None:
            return None
        return (self.invoice_date - self.order_date).days

    @property
    def invoice_to_cash_days(self) -> Optional[int]:
        return None if self.cash_reason else self.days_to_pay

    @property
    def order_to_cash_days(self) -> Optional[int]:
        """The whole cycle for this invoice, or ``None`` if either leg is.

        Measured on this invoice's own two dates. It happens to equal the two
        legs added, and it is written as the sum only because both legs share
        the invoice date they are measured across — the aggregate below is what
        must never be built that way.
        """
        first, second = self.order_to_invoice_days, self.invoice_to_cash_days
        if first is None or second is None:
            return None
        return first + second

    def days_for(self, stage_key: str) -> Optional[int]:
        return {
            "ORDER_TO_INVOICE": self.order_to_invoice_days,
            "INVOICE_TO_CASH": self.invoice_to_cash_days,
            "ORDER_TO_CASH": self.order_to_cash_days,
        }[stage_key]

    def reason_for(self, stage_key: str) -> Optional[str]:
        """Which missing evidence stops this stage being measured.

        The total stage reports the *first* leg that is unknown rather than
        inventing a reason of its own: an invoice with no order and no payment
        is unmeasurable for the order reason first, and a screen that said
        "still owed" about a counter sale nobody has paid would be naming the
        wrong problem.
        """
        if stage_key == "ORDER_TO_INVOICE":
            return self.order_reason
        if stage_key == "INVOICE_TO_CASH":
            return self.cash_reason
        return self.order_reason or self.cash_reason

    def to_dict(self) -> dict:
        return {
            "invoice_ref": self.invoice_ref,
            "invoice_number": self.invoice_number,
            "customer_id": self.customer_id,
            "customer_label": self.customer_label,
            "invoice_date": self.invoice_date.isoformat(),
            "order_date": (self.order_date.isoformat()
                           if self.order_date else None),
            "order_numbers": list(self.order_numbers),
            "orders_linked": self.orders_linked,
            #: True where one invoice bills against more than one order — the
            #: case the earliest-order rule reads down to a single figure.
            "consolidated": self.orders_linked > 1,
            "order_to_invoice_days": self.order_to_invoice_days,
            "invoice_to_cash_days": self.invoice_to_cash_days,
            "order_to_cash_days": self.order_to_cash_days,
            "order_unknown_reason": self.order_reason,
            "cash_unknown_reason": self.cash_reason,
        }


def _summarise(stage: Stage, cycles: list[Cycle]) -> dict:
    """One stage's figures, and an honest account of what it could not measure.

    The unknowns are reported beside the measured figures rather than under
    them. A stage measured on eleven invoices out of four hundred is a different
    claim from one measured on all four hundred, and a median printed without
    that number reads as the second.
    """
    measured = [c for c in cycles if c.days_for(stage.key) is not None]
    days = [c.days_for(stage.key) for c in measured]
    days = [d for d in days if d is not None]        # narrows for the checker

    unknown_by_reason: dict[str, int] = {}
    for c in cycles:
        reason = c.reason_for(stage.key)
        if c.days_for(stage.key) is None and reason:
            unknown_by_reason[reason] = unknown_by_reason.get(reason, 0) + 1

    estimable = len(days) >= MIN_OBSERVATIONS
    return {
        "key": stage.key,
        "label": stage.label,
        "owner": stage.owner,
        "owner_label": OWNERS[stage.owner],
        "question": stage.question,
        "measured": len(days),
        "unknown": len(cycles) - len(days),
        "unknown_by_reason": unknown_by_reason,
        # Median rather than mean, for the reason `payments` gives: one invoice
        # settled nine months late is a story about that invoice.
        "median_days": (round(statistics.median(days), 1) if estimable else None),
        # The slow tenth, through the one percentile in this package.
        "slow_days": (payments.percentile(days, 0.90) if estimable else None),
        "worst_days": (max(days) if days else None),
        #: Legs whose end date falls before their start date, so the day count
        #: is negative. Real at every stage and meaning something different at
        #: each: an invoice raised before the order it bills against is a
        #: back-dated order, a payment before the invoice is a prepayment
        #: against a known invoice, and cash before the order is both. Counted
        #: and left in the median rather than clipped to zero — clipping would
        #: hide the finding and flatter the figure in the same move.
        "before_start": sum(1 for d in days if d < 0),
        "estimable": estimable,
        "min_observations": MIN_OBSERVATIONS,
    }


def _split(cycles: list[Cycle]) -> dict:
    """Whose wait the cycle actually is, over the invoices that can answer both.

    Measured on the subset where **both** legs are known, so the two halves are
    comparable. Taking each leg's median over its own larger population would
    compare a figure drawn from four hundred invoices against one drawn from
    eleven and present the difference as a fact about the business.

    The share is Σ days ÷ Σ days, never the mean of per-invoice shares — the
    same aggregation rule ``CLAUDE.md`` §1 states for margin, and for the same
    reason: a small invoice that sat for a year would otherwise weigh as much
    as the rest of the quarter.
    """
    both = [c for c in cycles if c.order_to_cash_days is not None]
    ours = [c.order_to_invoice_days for c in both]
    theirs = [c.invoice_to_cash_days for c in both]
    total = sum(c.order_to_cash_days or 0 for c in both)
    estimable = len(both) >= MIN_OBSERVATIONS
    return {
        "invoices": len(both),
        "estimable": estimable,
        "ours_median_days": (round(statistics.median([d for d in ours if d is not None]), 1)
                             if estimable else None),
        "theirs_median_days": (round(statistics.median([d for d in theirs if d is not None]), 1)
                               if estimable else None),
        "total_median_days": (round(statistics.median(
            [c.order_to_cash_days or 0 for c in both]), 1) if estimable else None),
        # `None` rather than 0.5 when nobody waited at all: a book whose every
        # measured cycle closed the same day has no split to apportion, and
        # halving it would assert one.
        "ours_share": (round(sum(c.order_to_invoice_days or 0 for c in both) / total, 4)
                       if estimable and total > 0 else None),
    }


def build(cycles: Iterable[Cycle], as_of: date) -> dict:
    """The cycle by stage, the invoices behind it, and what could not be read."""
    rows = list(cycles)
    stages = [_summarise(stage, rows) for stage in STAGES]
    linked = sum(1 for c in rows if c.orders_linked)
    return {
        "as_of": as_of.isoformat(),
        "stages": stages,
        "split": _split(rows),
        "invoices": [c.to_dict() for c in rows],
        "coverage": {
            "invoices": len(rows),
            #: Invoices naming at least one order, whether or not it is held.
            "with_order": linked,
            "without_order": len(rows) - linked,
            "consolidated": sum(1 for c in rows if c.orders_linked > 1),
            #: Named an order and none of them is on record here. Separated
            #: from ``without_order`` because one is a counter sale and the
            #: other is a window this platform has not pulled.
            "order_not_held": sum(1 for c in rows
                                  if c.order_reason == "ORDER_NOT_HELD"),
        },
        "rule": RULE,
        "owners": OWNERS,
        "unknown_reasons": UNKNOWN_REASONS,
        "min_observations": MIN_OBSERVATIONS,
    }
