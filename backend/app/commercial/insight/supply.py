"""Who supplies this book, what is still outstanding, and how long it takes.

**Lead time is measured only where a receipt was logged.** Zoho records a
purchase order's receives; where there are none the order is either still open
or the receipt was never entered, and those are not the same thing. Neither is
converted into a lead time. An "average lead time" computed over orders that
were never marked received would be an average of the orders somebody
remembered to close, which is a statement about admin, not about suppliers.

**Promised dates are absent from this book.** ``expected_delivery_date`` is
blank on effectively every order here, so "late against promise" has no promise
side and is reported as unanswerable rather than measured against an assumed
lead time. Order *age* is answerable and is what the open list ranks by.

**Concentration is by spend, and the tail is not folded.** Unlike the customer
mix, a supplier list is short and every name on it is someone with a phone
number — an "Other (14)" band would hide exactly the second sources a
concentration question exists to find.

**The credit-note rate is reported only where a zero would have meant
something.** ``11-procurement.md`` §3 names this as one of the three supplier
dimensions this book has evidence for, once vendor credits are read — which they
now are. The trap it walks into if built naively is the one the reorder group
walked into: almost every supplier has issued no credit, and "0 of 4 bills" is
not a clean record, it is four bills. So the floor is derived from the book's own
credit rate rather than picked — how many bills this supplier would have had to
send before a zero became surprising — and below it the rate is ``None`` with the
counts attached, never a flattering nought.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from . import absence

#: Orders older than this with stock still to come. Not a promise breach —
#: there is no promise — but long enough to be worth a phone call.
STALE_ORDER_DAYS = 45

#: Fewer received orders than this and a "typical lead time" is one delivery.
MIN_RECEIPTS_FOR_LEAD_TIME = 3

#: A pair needs two purchases before its cost has moved at all. One purchase is
#: not a stable price, it is a price.
MIN_PURCHASES_FOR_A_MOVE = 2

#: And a supplier needs this many such items before a "typical" spread is a
#: statement about the supplier rather than about one line. Same number and same
#: reason as ``MIN_RECEIPTS_FOR_LEAD_TIME``.
MIN_ITEMS_FOR_PRICE_STABILITY = 3

#: How unlikely a supplier's clean record has to be, under the book's own credit
#: rate, before it is reported as a rate rather than as "not enough bills".
#:
#: Derived rather than picked, and per book rather than fixed, for the reason the
#: stock filters give about rupee cutoffs: on a book where 4% of bills draw a
#: credit, a supplier needs ~73 bills before a nought is worth reading; where the
#: rate is 10% it needs ~28. A single hardcoded minimum is wrong on at least one
#: of those, and wrong in the direction that reports a clean record nobody
#: measured.
CLEAN_RECORD_SURPRISING_AT = 0.05


@dataclass
class SupplierOrder:
    vendor_id: Optional[str]
    vendor_label: str
    number: Optional[str]
    ordered_on: date
    expected_on: Optional[date]
    received_on: Optional[date]
    pending_qty: float
    ordered_qty: float
    total: Optional[float]
    status: str

    @property
    def open(self) -> bool:
        return self.pending_qty > 0

    @property
    def lead_time_days(self) -> Optional[int]:
        """Only where a receipt exists. See the module docstring."""
        if self.received_on is None:
            return None
        return (self.received_on - self.ordered_on).days

    def age_days(self, as_of: date) -> int:
        return (as_of - self.ordered_on).days

    def to_dict(self, as_of: date) -> dict:
        return {
            "vendor_id": self.vendor_id, "vendor_label": self.vendor_label,
            "number": self.number,
            "ordered_on": self.ordered_on.isoformat(),
            "expected_on": self.expected_on.isoformat() if self.expected_on else None,
            "received_on": self.received_on.isoformat() if self.received_on else None,
            "ordered_qty": self.ordered_qty, "pending_qty": self.pending_qty,
            "total": self.total, "status": self.status,
            "age_days": self.age_days(as_of),
            "lead_time_days": self.lead_time_days,
        }


@dataclass(frozen=True)
class ItemCostRange:
    """What one (supplier, item) pair's unit cost has ranged over.

    ``11-procurement.md`` §3's third answerable supplier dimension, and it comes
    straight out of ``cost_records`` — no forecast, no band, no threshold. The
    spread is a summary of what happened, in the same register as
    ``typical_lead_time``: measured, not judged.

    **What a spread cannot tell you, said here because the number invites the
    wrong reading.** A supplier that raised its price once at the annual revision
    and a supplier whose price bounces on every order can produce the *same*
    spread, and only the second is unstable — the first is perfectly predictable
    and merely more expensive than it was. Separating them needs the ordered
    series (how far the cost ended up from where it started, against how far it
    ranged in between), and this deliberately does not carry it: the aggregate
    below is computed from one ``GROUP BY`` over bill lines, and fetching the
    ordered series for every pair to answer a second question is a cost this
    screen has not been asked to pay. Read a large spread as "worth opening the
    line", not as "this supplier is erratic".

    **Ratios only, no rupees.** ``/supply`` is manager-and-above, so cost would
    be permitted here — but a spread is scale-free and the levels are not, and a
    scorecard needs how far a price moved rather than what it was. Keeping the
    payload to ratios is what would let this dimension be shown more widely
    later without the question being reopened.
    """

    vendor_id: Optional[str]
    product_id: str
    purchases: int
    lowest: Decimal
    highest: Decimal

    @property
    def spread(self) -> Optional[float]:
        """``(highest - lowest) / lowest`` — how far this cost has ranged.

        ``None`` below the floor, and ``None`` where the lowest recorded cost is
        zero or negative: a free line is a data-entry artefact, and dividing by
        it would report an infinite spread as the supplier's headline number.
        """
        if self.purchases < MIN_PURCHASES_FOR_A_MOVE or self.lowest <= 0:
            return None
        return round(float((self.highest - self.lowest) / self.lowest), 4)


@dataclass(frozen=True)
class VendorBills:
    """One supplier's bills, and how many of them a credit was set against.

    Counts of documents, deliberately, not values. A value ratio would be a
    fraction of purchase spend — cost by another name, in exactly the sense that
    scopes this whole screen — and it would answer a worse question anyway: a
    scorecard asks how often this supplier gets an order wrong, not how much the
    corrections came to.
    """

    vendor_id: Optional[str]
    bills: int
    credited_bills: int


def min_bills_for_a_credit_rate(book_rate: float) -> Optional[int]:
    """How many bills make a clean record evidence rather than a small sample.

    ``(1 - p) ** n <= CLEAN_RECORD_SURPRISING_AT``, solved for *n*: the number of
    bills after which a supplier with no credits against any of them would be
    surprising, under the rate the rest of the book runs at.

    ``None`` where the question cannot be asked — a book whose own credit rate is
    zero says nothing about how many bills a clean supplier needs, because it has
    never seen a credit at all. Answering with a number there would let a book
    that simply has not read its vendor credits report every supplier as clean,
    which is the reorder-point mistake in a different column.
    """
    if book_rate <= 0.0:
        return None
    if book_rate >= 1.0:
        return 1
    return max(1, math.ceil(math.log(CLEAN_RECORD_SURPRISING_AT)
                            / math.log(1.0 - book_rate)))


@dataclass
class SupplierSpend:
    vendor_id: Optional[str]
    label: str
    spend: float
    orders: int
    open_orders: int
    lead_times: list[int]
    payment_terms_days: Optional[int]
    #: Bills seen from this supplier, and how many drew a vendor credit. Both
    #: are ``0`` for a supplier whose bills this pull has not read — which is
    #: why the rate below refuses rather than dividing.
    bills: int = 0
    credited_bills: int = 0
    #: Every item bought from this supplier more than once, and how far each
    #: one's unit cost has ranged. Empty for a supplier whose lines were never
    #: repeated — which is most of a distributor's catalogue, and the reason the
    #: figure below refuses rather than averaging one item.
    cost_spreads: tuple[float, ...] = ()

    @property
    def typical_price_spread(self) -> Optional[float]:
        """The median spread across the items bought from this supplier twice.

        Median rather than mean, for the reason ``typical_lead_time`` uses one:
        a single line whose cost trebled should not become the supplier's
        headline. ``None`` below ``MIN_ITEMS_FOR_PRICE_STABILITY``, because a
        "typical" over two items is two items.
        """
        if len(self.cost_spreads) < MIN_ITEMS_FOR_PRICE_STABILITY:
            return None
        return round(statistics.median(self.cost_spreads), 4)

    def credit_rate(self, floor: Optional[int]) -> Optional[float]:
        """Credited bills over bills, or ``None`` when a zero proves nothing.

        **The floor gates the clean claim, not every claim** — the first version
        of this applied it to both, and a review caught what that costs. The
        floor answers one question: after how many bills is *no* credit
        surprising. A supplier with three credits against four bills is far past
        any evidence bar for a non-zero rate, and gating it on the same number
        hid the one supplier whose record most warranted opening. The screen's
        footnote filters on a present rate, so hidden there meant absent
        entirely.

        So: a credit that happened is evidence of itself and reports whatever
        the sample is, with the counts beside it. A clean run is only evidence
        once it is long enough — ``floor is None`` means the book has read no
        credits at all and no supplier's record is measured; ``bills < floor``
        means this one has not sent enough for a nought to mean anything.
        """
        if self.bills <= 0:
            return None
        if self.credited_bills > 0:
            return round(self.credited_bills / self.bills, 4)
        if floor is None or self.bills < floor:
            return None
        return 0.0

    @property
    def typical_lead_time(self) -> Optional[float]:
        if len(self.lead_times) < MIN_RECEIPTS_FOR_LEAD_TIME:
            return None
        return round(statistics.median(self.lead_times), 1)

    def to_dict(self, total_spend: float,
                credit_rate_floor: Optional[int] = None) -> dict:
        return {
            "vendor_id": self.vendor_id, "label": self.label,
            "spend": round(self.spend, 2),
            "share": round(self.spend / total_spend, 4) if total_spend else 0.0,
            "orders": self.orders, "open_orders": self.open_orders,
            "typical_lead_time_days": self.typical_lead_time,
            "receipts_seen": len(self.lead_times),
            "min_receipts_for_lead_time": MIN_RECEIPTS_FOR_LEAD_TIME,
            "payment_terms_days": self.payment_terms_days,
            # The counts go out whatever the rate does. They are what makes a
            # null explainable on the screen — "no rate" beside "2 bills" is a
            # sample size; "no rate" on its own is a bug report waiting to be
            # filed.
            "bills": self.bills,
            "credited_bills": self.credited_bills,
            "credit_rate": self.credit_rate(credit_rate_floor),
            "min_bills_for_credit_rate": credit_rate_floor,
            # Same shape as the lead-time pair above: the figure, and the sample
            # it was allowed to come from. A null spread beside "1 repeat item"
            # is a catalogue that turns over slowly; a null with no count is a
            # screen nobody can debug.
            "typical_price_spread": self.typical_price_spread,
            "repeat_bought_items": len(self.cost_spreads),
            "min_items_for_price_spread": MIN_ITEMS_FOR_PRICE_STABILITY,
        }


def build(orders: Iterable[SupplierOrder], as_of: date, *,
          terms_by_vendor: Optional[dict[str, int]] = None,
          bills_by_vendor: Optional[dict[Optional[str], VendorBills]] = None,
          cost_ranges: Optional[Iterable[ItemCostRange]] = None,
          ) -> dict:
    """Supplier concentration, what is outstanding, and measured lead times.

    ``bills_by_vendor`` is optional and its absence is honest: a connection whose
    grant does not include bills, or one syncing before vendor credits existed,
    supplies nothing here and every supplier's credit rate is ``None`` rather
    than a clean sheet nobody earned.
    """
    rows = list(orders)
    terms = terms_by_vendor or {}
    bills = bills_by_vendor or {}
    spreads: dict[Optional[str], list[float]] = {}
    for r in cost_ranges or ():
        value = r.spread
        if value is not None:
            spreads.setdefault(r.vendor_id, []).append(value)
    if not rows:
        return {"as_of": as_of.isoformat(), "suppliers": [], "open_orders": [],
                "counts": {}, "unavailable": _unavailable(0, 0)}

    grouped: dict[Optional[str], SupplierSpend] = {}
    for o in rows:
        entry = grouped.get(o.vendor_id)
        if entry is None:
            seen = bills.get(o.vendor_id)
            entry = SupplierSpend(
                vendor_id=o.vendor_id, label=o.vendor_label, spend=0.0, orders=0,
                open_orders=0, lead_times=[],
                payment_terms_days=terms.get(o.vendor_id or ""),
                bills=seen.bills if seen else 0,
                credited_bills=seen.credited_bills if seen else 0,
                cost_spreads=tuple(spreads.get(o.vendor_id, ())))
            grouped[o.vendor_id] = entry
        entry.spend += float(o.total or 0.0)
        entry.orders += 1
        if o.open:
            entry.open_orders += 1
        lead = o.lead_time_days
        if lead is not None and lead >= 0:
            entry.lead_times.append(lead)

    total_spend = sum(e.spend for e in grouped.values())
    suppliers = sorted(grouped.values(), key=lambda e: e.spend, reverse=True)

    # The book's own rate, over every bill it has read — not over the suppliers
    # on this screen. A vendor with bills and no purchase orders is invisible
    # here (this list is built from orders) and still belongs in the
    # denominator: the floor is a property of how often *this book* draws a
    # credit, not of who happens to appear above.
    book_bills = sum(v.bills for v in bills.values())
    book_credited = sum(v.credited_bills for v in bills.values())
    book_rate = (book_credited / book_bills) if book_bills else 0.0
    credit_floor = min_bills_for_a_credit_rate(book_rate)

    open_orders = [o for o in rows if o.open]
    # Oldest first: with no promised date, age is the only thing that ranks
    # "which of these should I chase".
    open_orders.sort(key=lambda o: o.ordered_on)

    received = [o for o in rows if o.lead_time_days is not None]
    promised = [o for o in rows if o.expected_on is not None]

    return {
        "as_of": as_of.isoformat(),
        "suppliers": [e.to_dict(total_spend, credit_floor) for e in suppliers],
        "open_orders": [o.to_dict(as_of) for o in open_orders[:40]],
        "counts": {
            "suppliers": len(suppliers),
            "orders": len(rows),
            "open_orders": len(open_orders),
            "stale_open_orders": sum(1 for o in open_orders
                                     if o.age_days(as_of) >= STALE_ORDER_DAYS),
            "orders_with_a_receipt": len(received),
            "orders_with_a_promised_date": len(promised),
            "bills_read": book_bills,
            "bills_with_a_credit": book_credited,
            "suppliers_with_a_credit_rate": sum(
                1 for e in suppliers if e.credit_rate(credit_floor) is not None),
            "repeat_bought_items": sum(len(v) for v in spreads.values()),
            "suppliers_with_a_price_spread": sum(
                1 for e in suppliers if e.typical_price_spread is not None),
        },
        "total_spend": round(total_spend, 2),
        # Concentration, stated rather than left to be read off a chart.
        "top_supplier_share": (round(suppliers[0].spend / total_spend, 4)
                               if suppliers and total_spend else None),
        "median_lead_time_days": (
            round(statistics.median(o.lead_time_days for o in received), 1)
            if len(received) >= MIN_RECEIPTS_FOR_LEAD_TIME else None),
        "stale_after_days": STALE_ORDER_DAYS,
        #: The book-wide rate the per-supplier floor was derived from, published
        #: so the floor can be checked rather than taken on trust.
        "book_credit_rate": round(book_rate, 4) if book_bills else None,
        "min_bills_for_credit_rate": credit_floor,
        "unavailable": _unavailable(
            len(promised), len(rows),
            bills_read=book_bills, credits_seen=book_credited,
            unmeasured=sum(1 for e in suppliers
                           if e.credit_rate(credit_floor) is None),
            without_a_spread=sum(1 for e in suppliers
                                 if e.typical_price_spread is None)),
    }


def _unavailable(promised: int, total: int, *, bills_read: int = 0,
                 credits_seen: int = 0, unmeasured: int = 0,
                 without_a_spread: int = 0) -> list[dict]:
    out = []
    if not bills_read:
        out.append({
            "series": "supplier_credit_rate",
            # TRANSIENT rather than PERMANENT: the bills arrive with the next
            # pull that has the grant for them. What is not transient is the
            # damage of guessing — with no bills read, every supplier has zero
            # credits against zero bills, and a screen that rendered that as a
            # rate would award a clean record to a book it had not looked at.
            "kind": absence.TRANSIENT,
            "reason": ("No supplier bills have been read for this book, so no "
                       "supplier's credit record is measured. A rate here "
                       "would be 0 of 0 shown as a clean sheet."),
        })
    elif not credits_seen:
        out.append({
            "series": "supplier_credit_rate",
            "kind": absence.TRANSIENT,
            "reason": (f"None of the {bills_read} bills read for this book has "
                       "a vendor credit against it. That may be true and it may "
                       "mean the vendor-credit scope was never granted, and "
                       "nothing here can tell those apart — so no supplier is "
                       "reported as having a clean record rather than an "
                       "unmeasured one."),
        })
    elif unmeasured:
        out.append({
            "series": "supplier_credit_rate",
            "kind": absence.TRANSIENT,
            "reason": (f"{unmeasured} supplier(s) have sent too few bills for a "
                       "credit rate to mean anything. The bill and credit counts "
                       "are on every row; the rate appears once a supplier has "
                       "sent enough that a clean run would have been surprising."),
        })
    if without_a_spread:
        out.append({
            "series": "supplier_price_spread",
            # TRANSIENT, and on this book it is a slow transient: a cost spread
            # needs an item bought from the same supplier twice, and
            # `08-intermittent-demand.md` measured that most of this catalogue
            # moves once. The figure appears for the lines that repeat, which
            # are the lines an annual negotiation is about anyway.
            "kind": absence.TRANSIENT,
            "reason": (f"{without_a_spread} supplier(s) have fewer than "
                       f"{MIN_ITEMS_FOR_PRICE_STABILITY} items bought from them "
                       "more than once, so there is no typical price movement to "
                       "report. One purchase is not a stable price, it is a "
                       "price."),
        })
    if promised == 0:
        out.append({
            "series": "delivery_against_promise",
            # A field nobody fills, not a limit. ``simulate`` blocks a whole
            # scenario on the same blank, which makes this the highest-value
            # COLLECTABLE in the package.
            "kind": absence.COLLECTABLE,
            "reason": ("No purchase order in this book carries a promised "
                       "delivery date, so there is nothing to be late against. "
                       "Order age is shown instead — it is answerable, and an "
                       "assumed lead time would not be."),
        })
    elif promised < total:
        out.append({
            "series": "delivery_against_promise",
            "kind": absence.COLLECTABLE,
            "reason": (f"Only {promised} of {total} orders carry a promised "
                       "date. The rest are ranked by age, which is what the "
                       "data supports."),
        })
    return out
