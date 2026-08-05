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


#: A customer whose days-to-pay swing by more than this around their own median
#: cannot be planned around, and that is a different problem from being slow.
#: Expressed in days rather than as a coefficient because a distributor thinks
#: in days: "give or take a fortnight" is a sentence somebody can act on.
ERRATIC_SPREAD_DAYS = 21

#: How much the second half of a customer's history has to move against the
#: first before it is called a trend rather than noise.
TREND_DAYS = 10

#: The named patterns, and what each one means as a job of work. A pattern with
#: no consequence attached is a label; these are the four different things a
#: person actually does about a payer.
PATTERNS: dict[str, dict[str, str]] = {
    "PROMPT": {
        "label": "Pays to terms",
        "meaning": "Settles on or before the due date, consistently. Nothing to "
                   "do; worth knowing before anyone tightens their terms.",
    },
    "PREDICTABLY_LATE": {
        "label": "Late, but predictable",
        "meaning": "Consistently over the due date by a similar margin. This is "
                   "a terms problem, not a collections problem — the terms on "
                   "record do not describe how this account actually pays.",
    },
    "ERRATIC": {
        "label": "Erratic",
        "meaning": "The spread is wide enough that no single number describes "
                   "them. The riskiest of the four: cash from this account "
                   "cannot be planned, whatever the average says.",
    },
    "TOO_FEW": {
        "label": "Not enough history",
        "meaning": "Fewer settled invoices than the floor. No pattern is "
                   "asserted rather than one being read out of two payments.",
    },
}

#: Direction of travel, kept separate from the pattern. A customer can be
#: erratic *and* improving, and collapsing the two would lose one of them.
TRENDS: dict[str, str] = {
    "IMPROVING": "Paying faster than they used to",
    "STEADY": "No material change",
    "DETERIORATING": "Paying slower than they used to",
    "UNKNOWN": "Too few settlements to compare two halves",
}


def _spread(days: list[int]) -> Optional[float]:
    """Median absolute deviation, not standard deviation.

    One invoice settled nine months late is a story about that invoice. A
    standard deviation would let it redefine the customer; the MAD does not,
    which is the same reason the headline figure is a median.
    """
    if len(days) < 2:
        return None
    mid = statistics.median(days)
    return round(statistics.median([abs(d - mid) for d in days]), 1)


def _trend(settled: list[Settlement]) -> str:
    """Compare the older half of their history against the newer half.

    By invoice date, not payment date: the question is whether the invoices
    they are raising now get paid faster than the ones they raised before.
    """
    if len(settled) < MIN_SETTLEMENTS * 2:
        return "UNKNOWN"
    ordered = sorted(settled, key=lambda s: s.invoice_date)
    mid = len(ordered) // 2
    before = statistics.median(s.days_to_pay for s in ordered[:mid])
    after = statistics.median(s.days_to_pay for s in ordered[mid:])
    if after - before >= TREND_DAYS:
        return "DETERIORATING"
    if before - after >= TREND_DAYS:
        return "IMPROVING"
    return "STEADY"


def classify(settled: list[Settlement]) -> dict:
    """How this customer pays — a pattern, a direction, and the evidence.

    Deterministic and derived only from settled invoices. Nothing here predicts
    whether they will pay next time; it describes what they have done, which is
    the only thing the data supports.
    """
    if len(settled) < MIN_SETTLEMENTS:
        return {"pattern": "TOO_FEW", "trend": "UNKNOWN", "spread_days": None,
                "median_days_late": None, "part_paid_invoices": 0,
                "largest_batch": 0}

    days = [s.days_to_pay for s in settled]
    spread = _spread(days)
    datable = [s for s in settled if s.days_late is not None]
    median_late = (statistics.median(s.days_late for s in datable   # type: ignore[misc]
                                     ) if datable else None)

    if spread is not None and spread > ERRATIC_SPREAD_DAYS:
        pattern = "ERRATIC"
    elif median_late is None:
        # No due date anywhere on record: their punctuality is unanswerable, so
        # it is not answered. Spread still says whether they are plannable.
        pattern = "PROMPT" if spread is not None and spread <= ERRATIC_SPREAD_DAYS else "ERRATIC"
    elif median_late > 0:
        pattern = "PREDICTABLY_LATE"
    else:
        pattern = "PROMPT"

    # Two habits worth naming because they change what a collections call is
    # about: an invoice settled in several instalments, and one payment
    # clearing a batch.
    by_invoice: dict[str, int] = {}
    by_payment_day: dict[date, int] = {}
    for s in settled:
        by_invoice[s.invoice_ref] = by_invoice.get(s.invoice_ref, 0) + 1
        by_payment_day[s.paid_on] = by_payment_day.get(s.paid_on, 0) + 1

    return {
        "pattern": pattern,
        "trend": _trend(settled),
        "spread_days": spread,
        "median_days_late": (round(median_late, 1) if median_late is not None else None),
        "part_paid_invoices": sum(1 for n in by_invoice.values() if n > 1),
        "largest_batch": max(by_payment_day.values()),
    }


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

    patterns: dict[str, dict] = {}
    customers: list[CustomerPayment] = []
    for customer_id, group in by_customer.items():
        patterns[customer_id] = classify(group)
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
        "customers": [{**c.to_dict(), **patterns[c.customer_id]} for c in customers],
        "patterns": PATTERNS,
        "trends": TRENDS,
        "pattern_counts": _counts(patterns),
        "erratic_spread_days": ERRATIC_SPREAD_DAYS,
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


def _counts(patterns: dict[str, dict]) -> dict[str, int]:
    out = {key: 0 for key in PATTERNS}
    for p in patterns.values():
        out[p["pattern"]] = out.get(p["pattern"], 0) + 1
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
