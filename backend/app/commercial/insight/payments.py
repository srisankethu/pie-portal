"""How long customers take to pay, measured rather than assumed.

This is the series the health timeline used to declare it could not draw. The
platform held invoice lines and no receipts, so days-to-pay had to be invented
or omitted, and it was omitted. Now that payments are ingested it is computed,
and the refusal in ``cohorts.health_timeline`` comes off — a screen that keeps
apologising for a gap it no longer has is a screen nobody reads carefully.

**Days-to-pay is per application, not per payment.** One bank transfer settling
ten invoices is ten observations, each with its own invoice date, and averaging
the payment instead would give a customer who batches their remittances a
flattering single data point. The row grain is the invoice.

**An advance is not a fast payment.** Money received against no invoice has no
invoice date to subtract, so it produces no observation at all. Counting it as
zero days would make every customer who pays up front look like the fastest
payer in the book, which is the opposite of what a prepayment means for risk.

**Late is measured against the due date; slow is measured against the invoice
date.** They answer different questions — "did they honour the terms" and "how
long is my cash tied up" — and an invoice with no due date on record can answer
the second but not the first.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

#: Fewer settled invoices than this and an "average days to pay" is one
#: transaction wearing a suit. Matches the spirit of the cadence floor.
MIN_SETTLEMENTS = 3


@dataclass(frozen=True)
class Settlement:
    """One invoice, settled. The grain everything here is computed at."""
    customer_id: str
    invoice_ref: str
    invoice_number: Optional[str]
    invoice_date: date
    due_date: Optional[date]
    paid_on: date
    amount: float

    @property
    def days_to_pay(self) -> int:
        return (self.paid_on - self.invoice_date).days

    @property
    def days_late(self) -> Optional[int]:
        """None when no due date is on record — not zero, which would read as
        "paid exactly on time" for an invoice whose terms nobody recorded."""
        if self.due_date is None:
            return None
        return (self.paid_on - self.due_date).days

    @property
    def late(self) -> bool:
        return (self.days_late or 0) > 0


@dataclass
class CustomerPayment:
    customer_id: str
    label: str
    settlements: int
    median_days_to_pay: Optional[float]
    worst_days_to_pay: Optional[int]
    late_count: int
    #: Of the settlements that *have* a due date. A share computed over
    #: invoices that could never be late would understate the problem.
    datable_count: int
    total_settled: float
    agreed_terms_days: Optional[int]

    @property
    def late_share(self) -> Optional[float]:
        return (self.late_count / self.datable_count) if self.datable_count else None

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id, "label": self.label,
            "settlements": self.settlements,
            "median_days_to_pay": self.median_days_to_pay,
            "worst_days_to_pay": self.worst_days_to_pay,
            "late_count": self.late_count,
            "datable_count": self.datable_count,
            "late_share": (round(self.late_share, 4)
                           if self.late_share is not None else None),
            "total_settled": round(self.total_settled, 2),
            "agreed_terms_days": self.agreed_terms_days,
            # Below the floor there is no rhythm to report. Named rather than
            # shown as a number derived from one or two invoices.
            "estimable": self.settlements >= MIN_SETTLEMENTS,
        }


def build(settlements: Iterable[Settlement], names: dict[str, str],
          as_of: date, *, advances: int = 0,
          unapplied_total: float = 0.0) -> dict:
    """The book's payment behaviour, and every customer's place in it."""
    rows = list(settlements)
    if not rows:
        return {"as_of": as_of.isoformat(), "customers": [], "distribution": [],
                "median_days_to_pay": None, "late_share": None,
                "advances": advances, "unapplied_total": round(unapplied_total, 2),
                "min_settlements": MIN_SETTLEMENTS}

    by_customer: dict[str, list[Settlement]] = {}
    for s in rows:
        by_customer.setdefault(s.customer_id, []).append(s)

    customers: list[CustomerPayment] = []
    for customer_id, group in by_customer.items():
        datable = [s for s in group if s.days_late is not None]
        customers.append(CustomerPayment(
            customer_id=customer_id,
            label=names.get(customer_id, customer_id),
            settlements=len(group),
            # Median, not mean: one invoice paid nine months late is a story on
            # its own, not evidence that the customer is a nine-month payer.
            median_days_to_pay=round(statistics.median(s.days_to_pay for s in group), 1),
            worst_days_to_pay=max(s.days_to_pay for s in group),
            late_count=sum(1 for s in datable if s.late),
            datable_count=len(datable),
            total_settled=float(sum(s.amount for s in group)),
            agreed_terms_days=None,
        ))

    # Slowest first, then by money at stake. The question is "whose cash is
    # tied up longest", and that is a rank.
    customers.sort(key=lambda c: (c.median_days_to_pay or 0, c.total_settled),
                   reverse=True)

    all_days = [s.days_to_pay for s in rows]
    datable_all = [s for s in rows if s.days_late is not None]

    return {
        "as_of": as_of.isoformat(),
        "customers": [c.to_dict() for c in customers],
        "distribution": _distribution(all_days),
        "median_days_to_pay": round(statistics.median(all_days), 1),
        "settlements": len(rows),
        "late_share": (round(sum(1 for s in datable_all if s.late) / len(datable_all), 4)
                       if datable_all else None),
        "undatable_count": len(rows) - len(datable_all),
        #: Money in with no invoice behind it. Reported beside the averages
        #: rather than folded into them.
        "advances": advances,
        "unapplied_total": round(unapplied_total, 2),
        "min_settlements": MIN_SETTLEMENTS,
        "note": ("Measured per invoice settled, not per payment: one transfer "
                 "clearing ten invoices is ten observations. Advances carry no "
                 "invoice date and are counted separately rather than as "
                 "same-day payments."),
    }


#: Buckets a distributor actually talks in. Fixed rather than quantile because
#: the question here is "are we being paid to terms", and terms are absolute —
#: a quantile band would move every time the book did and 30 days would stop
#: meaning 30 days.
_BUCKETS: tuple[tuple[str, int, Optional[int]], ...] = (
    ("0–15 days", 0, 15),
    ("16–30 days", 16, 30),
    ("31–45 days", 31, 45),
    ("46–60 days", 46, 60),
    ("61–90 days", 61, 90),
    ("over 90 days", 91, None),
)


def _distribution(days: list[int]) -> list[dict]:
    out = []
    for label, lo, hi in _BUCKETS:
        n = sum(1 for d in days if d >= lo and (hi is None or d <= hi))
        out.append({"label": label, "from": lo, "to": hi, "count": n})
    # Paid before the invoice was raised: real, and it is a prepayment against
    # a known invoice rather than a negative duration to be hidden.
    early = sum(1 for d in days if d < 0)
    if early:
        out.insert(0, {"label": "paid in advance of the invoice",
                       "from": None, "to": -1, "count": early})
    return out


def monthly_series(settlements: Iterable[Settlement], periods) -> list[dict]:
    """Median days-to-pay per month, for the customer health timeline.

    Keyed on the month the invoice was *raised*, not the month it was paid, so
    the point sits beside that month's revenue and orders. A payment series
    indexed by payment date would put January's cash on the March row and make
    the three rows describe different things.
    """
    rows = list(settlements)
    out = []
    for period in periods:
        in_period = [s for s in rows if period.contains(s.invoice_date)]
        out.append({
            **period.to_dict(),
            "settled": len(in_period),
            "median_days_to_pay": (
                round(statistics.median(s.days_to_pay for s in in_period), 1)
                if in_period else None),
        })
    return out
