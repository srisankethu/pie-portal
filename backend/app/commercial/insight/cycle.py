"""How long a rupee is tied up between paying for stock and being paid for it.

The cash conversion cycle — ``DIO + DSO − DPO``, in days — for each legal entity,
at every month end. Zoho reports a single-period turnover ratio for one company;
what it will not give is the composite, the trend behind it, or three books side
by side. That triple is what this module exists for.

**It belongs here and not in ``state/``.** ``state/reducers/receivables`` refuses
DSO in as many words — "DSO needs a revenue window this state does not hold" —
and that refusal is right *about the fold*. A fold is keyed by party and holds a
current position; a window denominator is a question somebody asks, not a fact to
accumulate. ``insight/`` already reads line grain and does windowed arithmetic,
which is exactly the shape of every leg below.

Nothing here is a new stored balance. The month-end positions are **replayed
from dated line data**, the same way ``insight/bonds`` replays a relationship's
strength at each month end rather than storing one — see that module for the
pattern this follows.

---

## The three legs, and what each one is actually made of

``DSO``  receivables ÷ billings × window days.

    Receivables at date *D* are reconstructed as **invoices raised ≤ D, minus
    payments applied ≤ D, minus credit applied ≤ D**. All three terms are dated
    at line grain (``InvoiceDoc``, ``PaymentApplication``,
    ``CreditNoteApplication``), so the position is a replay and not an estimate.

    The third term is the one that is easy to leave out and expensive to leave
    out: omitting applied credit overstates the receivable by exactly the credit
    issued, in the direction that flatters collection performance. It is the
    reason ``CreditNoteApplication`` exists.

    **This is not the receivables fold, and must never become it.**
    ``InvoiceDoc.balance`` already nets applied credit because Zoho nets it, and
    ``state/reducers/receivables`` reads that balance. Subtracting credit there
    as well would count it twice. The two live side by side on purpose: the fold
    answers "what is owed now", this answers "what was owed then", and only the
    second one needs the credit term.

``DPO``  payables ÷ **COGS** × window days.

    Payables at *D* are bills raised ≤ D minus bill payments applied ≤ D
    (``BillDoc``, ``BillPaymentApplication``).

    **The denominator is cost of goods sold, not purchase spend, and the choice
    is deliberate.** Spend is what was bought in the window; COGS is what was
    sold out of stock in it. For a distributor whose buying is lumpy — one large
    replenishment order lands in a month and nothing the next — a spend
    denominator makes DPO swing by a factor of three on purchasing rhythm alone,
    which says nothing about how the business pays. COGS is the measure that
    matches DIO's denominator too, so ``DIO − DPO`` is a difference of two
    figures over the same base rather than two unrelated ratios subtracted.
    COGS is taken from ``commercial/economics.line_economics`` — the one
    definition of what a sold line cost — never recomputed here.

    The two are never mixed across months: every month in a series uses COGS,
    and there is no fallback to spend for a month where COGS is thin. A thin
    month is UNKNOWN (below).

``DIO``  inventory ÷ COGS × window days.

    **And this is the leg that limits the whole trend.** Zoho reports stock as a
    level *now* and holds no history at all, so inventory history exists only
    where ``StockSnapshot`` wrote it down — from the day this platform started
    observing, and not one day earlier. It cannot be backfilled from anything.

    So: **a month with no stock observation inside it has no DIO, and therefore
    no CCC.** Not the previous month's value carried forward, not the nearest
    observation borrowed, not a quietly shortened window. A CCC whose inventory
    leg is unbacked is a wrong number wearing a decimal point, and it is wrong in
    a direction nobody can predict.

## What makes a month UNKNOWN

Every refusal below is computed, not asserted, and each one names itself in the
month's ``why``:

``dio``  no stock observation inside the month, or nothing at that observation
         could be valued (§ ``purchase_rate`` is blank on part of the master).
``dso``  the trailing window opens before receivables can be reconstructed —
         see *how far back this is trustworthy* — or nothing was billed in it.
``dpo``  the same boundary on the payable side, or COGS is unknown.
``cogs`` fewer than ``min_cost_coverage`` of the window's sold lines carry an
         applicable cost record. Summing COGS over only the costed lines would
         understate it and inflate both DIO and DPO; the platform's own evidence
         floor already has an opinion about this and it is reused rather than a
         second one being invented here.

``ccc`` is stated only when all three legs are. It is never the sum of the legs
that happen to be known.

## How far back this is trustworthy, and how that is decided

Reconstruction needs the invoice, not only the payment. ``PaymentApplication``
deliberately stores the invoice's own date so a payment landing today can settle
an invoice from before the sync window — which means the platform routinely holds
*applications against invoices it does not hold*. Each one is evidence that a
receivable existed which this reconstruction cannot see.

So the boundary is computed from that evidence rather than guessed:

    reliable from = the later of
        the earliest invoice on record for that book, and
        the day after the latest application against an invoice not on record

Unmatched applications are also **excluded from the subtraction** — subtracting a
payment for an invoice that was never added would drive the position negative.
They are counted and reported instead.

The residual, stated because it is real: an invoice raised before the sync window
and *still unpaid today* leaves no trace at all, so it moves no boundary and is
simply missing. The boundary is the tightest one the data can support, not a
guarantee.

## Per entity, and no pooled total

Every series is per connected company. The same customer or supplier trading with
two of the books is two relationships, and a cycle read across them silently
merges three balance sheets into one that belongs to no legal entity — a number
with no filing behind it. There is deliberately **no "all books" series**, and a
record whose master carries no connection is left out and counted rather than
folded into a fourth bucket.

Layer rules, inherited: this is ``commercial/``, so it is deterministic and never
imports ``ai/``. Money is ``Decimal``; days are days; ratios are ratios.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from ..config import CommercialThresholds
from . import absence, periods

#: How many month-end points a series carries by default.
DEFAULT_MONTHS = 12

#: How long each point's denominators are measured over, in whole calendar
#: months. A single month is too short for a distributor: one large tender
#: invoiced on the 28th halves that month's DSO and doubles the next one's,
#: and the reader sees a cycle that swung by forty days when the business did
#: not change. Three months is long enough to absorb one order cycle and short
#: enough that a real deterioration still shows within a quarter.
WINDOW_MONTHS = 3

_ZERO = Decimal(0)
_ONE_DAY = timedelta(days=1)

#: The legs, in the order the identity reads. Named once so the response, the
#: refusal reasons and the accessible table cannot disagree about the order.
LEGS = ("dio", "dso", "dpo")


# ── inputs ───────────────────────────────────────────────────────────────────
#
# Plain dated facts, resolved by the caller. The router knows how to turn a
# customer into the book it belongs to and a sale line into its cost basis; this
# module knows what to do with the result. The same arrangement `bonds.TradeLine`
# uses, and for the same reason — it keeps `commercial/` free of a database
# handle and keeps the arithmetic testable without one.


@dataclass(frozen=True)
class Document:
    """An invoice or a bill: money owed one way or the other, from a date.

    One type for both sides because they are the same fact pointed in opposite
    directions, and two mirrored dataclasses would be the place the two halves
    started disagreeing about what a total is. ``ref`` is the source system's
    own id, which is what an application below is matched against.
    """

    book: str
    date: date
    ref: str
    total: Decimal


@dataclass(frozen=True)
class Applied:
    """One receipt, credit note or outbound payment set against one document.

    ``document_ref`` is matched against ``Document.ref``. An application naming a
    document this platform does not hold is *not* dropped quietly: it is what
    moves the trustworthy-from boundary, so it is counted and reported.
    """

    book: str
    on: date
    document_ref: str
    amount: Decimal


@dataclass(frozen=True)
class Sold:
    """One sale line, with what it cost — or honestly without.

    ``cogs`` is ``None`` where no applicable cost record exists, exactly as
    ``economics.LineEconomics`` leaves it. Never zero: a line costed at zero
    would report a 100% margin on the platform's own ignorance, and here it
    would understate COGS and inflate both DIO and DPO.
    """

    book: str
    date: date
    revenue: Decimal
    cogs: Optional[Decimal]


@dataclass(frozen=True)
class Held:
    """One item's stock on one observation day, and what a unit of it cost.

    ``unit_cost`` is ``None`` where the item master carries no purchase rate.
    A line with nothing on the shelf is worth zero whatever its rate says, which
    is why the two conditions are resolved here rather than by the caller —
    "worth nothing" and "cannot be valued" are different answers and only one of
    them is a gap.
    """

    book: str
    on: date
    units: Decimal
    unit_cost: Optional[Decimal]

    @property
    def value(self) -> Optional[Decimal]:
        if self.units <= _ZERO:
            return _ZERO
        if self.unit_cost is None:
            return None
        return self.units * self.unit_cost


# ── the result ───────────────────────────────────────────────────────────────
@dataclass
class Month:
    """One month end for one book: the three legs, and what is missing."""

    period: periods.Period
    window: periods.Period
    window_days: int

    receivables: Optional[Decimal] = None
    payables: Optional[Decimal] = None
    inventory: Optional[Decimal] = None
    billed: Decimal = _ZERO
    cogs: Optional[Decimal] = None
    cost_coverage: Optional[float] = None
    stock_observed_on: Optional[date] = None
    items_valued: int = 0
    items_unvalued: int = 0

    dio: Optional[float] = None
    dso: Optional[float] = None
    dpo: Optional[float] = None
    why: dict[str, str] = field(default_factory=dict)

    @property
    def ccc(self) -> Optional[float]:
        """The composite, or nothing. Never a sum of the legs that survived."""
        if self.dio is None or self.dso is None or self.dpo is None:
            return None
        return round(self.dio + self.dso - self.dpo, 1)

    def to_dict(self) -> dict:
        return {
            "month": self.period.label,
            "ends_on": self.period.end.isoformat(),
            "dio": self.dio, "dso": self.dso, "dpo": self.dpo, "ccc": self.ccc,
            "unknown": [leg for leg in LEGS if getattr(self, leg) is None],
            "why": dict(self.why),
            "receivables": _money(self.receivables),
            "payables": _money(self.payables),
            "inventory": _money(self.inventory),
            "billed": _money(self.billed),
            "cogs": _money(self.cogs),
            "cost_coverage": self.cost_coverage,
            "stock_observed_on": (self.stock_observed_on.isoformat()
                                  if self.stock_observed_on else None),
            "items_valued": self.items_valued,
            "items_unvalued": self.items_unvalued,
            "window": {"start": self.window.start.isoformat(),
                       "end": self.window.end.isoformat(),
                       "days": self.window_days},
        }


def _money(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(round(value, 2))


def _days(numerator: Decimal, denominator: Optional[Decimal],
          window_days: int) -> Optional[float]:
    """``numerator ÷ denominator × days``, or nothing at all.

    A zero or negative denominator is not a very large cycle, it is an
    unanswerable one — dividing by a quarter in which nothing was billed or
    nothing was sold produces an infinity that renders as a plausible number.
    """
    if denominator is None or denominator <= _ZERO:
        return None
    return round(float(numerator / denominator) * window_days, 1)


# ── the build ────────────────────────────────────────────────────────────────
def build(*, invoices: Iterable[Document], receipts: Iterable[Applied],
          credits: Iterable[Applied], bills: Iterable[Document],
          bill_payments: Iterable[Applied], sold: Iterable[Sold],
          held: Iterable[Held], books: dict[str, str], as_of: date,
          thresholds: CommercialThresholds, months: int = DEFAULT_MONTHS,
          window_months: int = WINDOW_MONTHS) -> dict:
    """One cash conversion cycle series per connected company.

    ``books`` maps a connection id to that company's name, and it is the *only*
    source of which entities exist — read from the connection list rather than
    from the rows on screen, for the reason ``routers/insight._companies``
    gives: a book synced before connections were stamped leaves every row's
    origin null, and a series derived from row provenance would silently
    disappear exactly when it is most needed.

    One pass over each input to bucket it, then one pass per month over the
    buckets. A book of a hundred thousand lines over twenty-four months is
    ``O(rows + 24)`` per book, which is why this is computed on request rather
    than needing a table of its own.
    """
    # `window_months - 1` extra months on the front so the earliest point still
    # has a full trailing window rather than a short one nobody chose.
    span = periods.months_back(as_of, months + window_months - 1)
    points = span[window_months - 1:]

    invoice_rows = list(invoices)
    bill_rows = list(bills)
    sold_rows = list(sold)
    held_rows = list(held)

    by_book = {
        connection_id: _render(one) for connection_id, one in _replay(
            invoices=invoice_rows, receipts=receipts, credits=credits,
            bills=bill_rows, bill_payments=bill_payments, sold=sold_rows,
            held=held_rows, books=books, span=span, points=points,
            window_months=window_months, thresholds=thresholds).items()
    }

    placed = set(books)
    unattributed = {
        "invoices": sum(1 for d in invoice_rows if d.book not in placed),
        "bills": sum(1 for d in bill_rows if d.book not in placed),
        "sale_lines": sum(1 for s in sold_rows if s.book not in placed),
        "stock_lines": sum(1 for h in held_rows if h.book not in placed),
    }

    entities = sorted(
        ({"connection_id": connection_id, **payload}
         for connection_id, payload in by_book.items()),
        key=lambda e: str(e["label"]))
    return {
        "as_of": as_of.isoformat(),
        "months": months,
        "window_months": window_months,
        "entities": entities,
        "definition": _definition(thresholds),
        "basis": _basis(),
        "unattributed": unattributed,
        "unavailable": _unavailable(held=held_rows),
        "min_cost_coverage": thresholds.min_cost_coverage,
        "thresholds_version": thresholds.version,
    }


def _split(rows: list[Applied],
           known: set[str]) -> tuple[list[Applied], list[Applied]]:
    """Applications against documents we hold, and against documents we do not.

    The second list is never silently dropped. Each entry is a document that was
    outstanding at some point and that this reconstruction cannot see, which is
    precisely what decides how far back the series may be believed.
    """
    matched = [a for a in rows if a.document_ref in known]
    return matched, [a for a in rows if a.document_ref not in known]


@dataclass(frozen=True)
class Replay:
    """One book's month-end rows, before anything renders them.

    Lifted out because a second consumer now exists. ``insight/capital`` asks
    what the cycle *costs* — the money standing in it, what that money earns,
    and what a rupee of extra revenue would add to it — and every one of those
    is arithmetic over exactly these rows. A module that re-read the documents
    to rebuild them would be a second reconstruction of the same positions, and
    the day the two disagreed there would be no way to say which screen was
    wrong.

    So the split is between *replaying* and *rendering*, not between two views.
    ``months`` carries ``Decimal`` positions and ``Optional`` legs; ``_render``
    turns them into the floats a chart wants. Money arithmetic happens on this
    side of that line, never on the rounded numbers.
    """

    connection_id: str
    label: str
    months: list[Month]
    receivable_from: Optional[date]
    payable_from: Optional[date]
    observation_days: tuple[date, ...]
    counts: dict[str, int]


def replay(*, invoices: Iterable[Document], receipts: Iterable[Applied],
           credits: Iterable[Applied], bills: Iterable[Document],
           bill_payments: Iterable[Applied], sold: Iterable[Sold],
           held: Iterable[Held], books: dict[str, str], as_of: date,
           thresholds: CommercialThresholds, months: int = DEFAULT_MONTHS,
           window_months: int = WINDOW_MONTHS) -> dict[str, Replay]:
    """The month-end rows ``build`` renders, per connected company.

    Same inputs, same window arithmetic, same refusals — this is the half of
    ``build`` that decides what is true, without the half that decides how it
    prints. A caller wanting the cycle for a screen wants ``build``; a caller
    computing something *from* the cycle wants this, so that whatever it derives
    inherits every refusal rather than reproducing a subset of them.
    """
    span = periods.months_back(as_of, months + window_months - 1)
    return _replay(invoices=list(invoices), receipts=receipts, credits=credits,
                   bills=list(bills), bill_payments=bill_payments,
                   sold=list(sold), held=list(held), books=books, span=span,
                   points=span[window_months - 1:], window_months=window_months,
                   thresholds=thresholds)


def _replay(*, invoices: list[Document], receipts: Iterable[Applied],
            credits: Iterable[Applied], bills: list[Document],
            bill_payments: Iterable[Applied], sold: list[Sold],
            held: list[Held], books: dict[str, str],
            span: list[periods.Period], points: list[periods.Period],
            window_months: int,
            thresholds: CommercialThresholds) -> dict[str, Replay]:
    """Bucket every input by the book it belongs to, then replay each book."""
    known_invoices = {d.ref for d in invoices}
    known_bills = {d.ref for d in bills}

    receivable_applied, receivable_unmatched = _split(
        list(receipts) + list(credits), known_invoices)
    payable_applied, payable_unmatched = _split(list(bill_payments), known_bills)

    return {
        connection_id: _one(
            connection_id=connection_id,
            label=label,
            invoices=[d for d in invoices if d.book == connection_id],
            receivable_applied=[a for a in receivable_applied
                                if a.book == connection_id],
            receivable_unmatched=[a for a in receivable_unmatched
                                  if a.book == connection_id],
            bills=[d for d in bills if d.book == connection_id],
            payable_applied=[a for a in payable_applied
                             if a.book == connection_id],
            payable_unmatched=[a for a in payable_unmatched
                               if a.book == connection_id],
            sold=[s for s in sold if s.book == connection_id],
            held=[h for h in held if h.book == connection_id],
            span=span, points=points, window_months=window_months,
            thresholds=thresholds)
        for connection_id, label in books.items()
    }


def _one(*, connection_id: str, label: str, invoices: list[Document],
         receivable_applied: list[Applied], receivable_unmatched: list[Applied],
         bills: list[Document], payable_applied: list[Applied],
         payable_unmatched: list[Applied], sold: list[Sold], held: list[Held],
         span: list[periods.Period], points: list[periods.Period],
         window_months: int, thresholds: CommercialThresholds) -> Replay:
    """One company's series, and the boundary it is believable from."""
    receivable_from = _reliable_from(invoices, receivable_unmatched)
    payable_from = _reliable_from(bills, payable_unmatched)
    observations = _observations(held)

    rows: list[Month] = []
    for i, period in enumerate(points):
        window_periods = span[i: i + window_months]
        window = periods.Period(
            start=window_periods[0].start, end=period.end,
            label=periods.label_for(window_periods[0].start, period.end))
        row = Month(period=period, window=window,
                    window_days=(window.end - window.start).days + 1)

        row.receivables = _position(invoices, receivable_applied, period.end)
        row.payables = _position(bills, payable_applied, period.end)
        row.billed = sum((d.total for d in invoices
                          if window.start <= d.date <= window.end), _ZERO)
        _cogs(row, sold, window, thresholds)
        _inventory(row, observations, period)

        _dso(row, receivable_from)
        _dpo(row, payable_from)
        _dio(row)
        rows.append(row)

    return Replay(
        connection_id=connection_id, label=label, months=rows,
        receivable_from=receivable_from, payable_from=payable_from,
        observation_days=tuple(sorted(observations)),
        counts={
            "invoices": len(invoices),
            "bills": len(bills),
            "receivable_applications_unmatched": len(receivable_unmatched),
            "payable_applications_unmatched": len(payable_unmatched),
            "stock_observation_days": len(observations),
        })


def _render(one: Replay) -> dict:
    """One replayed company as the series a screen reads."""
    rows = one.months
    stated = [r for r in rows if r.ccc is not None]
    latest = stated[-1] if stated else None
    previous = stated[-2] if len(stated) > 1 else None
    return {
        "label": one.label,
        "months": [r.to_dict() for r in rows],
        # The two numbers a reader acts on, lifted out rather than left to be
        # found at the end of an array. `change_days` is days, not percentage
        # points — a cycle is a duration, and a movement in one is a duration
        # too.
        #
        # Both are taken from the *stated* months, so `change_days` can span a
        # gap: with March unstated, June's movement is against February. That is
        # the only honest comparison available and the screen says which month
        # it is against rather than "last month".
        "latest": latest.to_dict() if latest else None,
        "previous_stated_month": previous.period.label if previous else None,
        "change_days": (round(latest.ccc - previous.ccc, 1)
                        if latest is not None and previous is not None
                        and latest.ccc is not None and previous.ccc is not None
                        else None),
        "months_stated": len(stated),
        "receivables_reliable_from": (one.receivable_from.isoformat()
                                      if one.receivable_from else None),
        "payables_reliable_from": (one.payable_from.isoformat()
                                   if one.payable_from else None),
        "stock_observed_from": (one.observation_days[0].isoformat()
                                if one.observation_days else None),
        "counts": dict(one.counts),
    }


def _reliable_from(documents: list[Document],
                   unmatched: list[Applied]) -> Optional[date]:
    """The earliest date a position can honestly be reconstructed for.

    The later of the first document on record and the day after the last
    application against a document that is not. Before the first, the position
    would be zero because nothing was read, not because nothing was owed; before
    the second, it is understated by documents whose existence the applications
    themselves prove.
    """
    if not documents:
        return None
    earliest = min(d.date for d in documents)
    if unmatched:
        return max(earliest, max(a.on for a in unmatched) + _ONE_DAY)
    return earliest


def _position(documents: list[Document], applied: list[Applied],
              on: date) -> Decimal:
    """Raised by ``on``, less everything set against it by ``on``.

    On the receivable side ``applied`` carries both receipts and credit notes,
    which is the whole point: leaving credit out overstates the balance in the
    direction that flatters collection.
    """
    raised = sum((d.total for d in documents if d.date <= on), _ZERO)
    settled = sum((a.amount for a in applied if a.on <= on), _ZERO)
    return raised - settled


def _cogs(row: Month, sold: list[Sold], window: periods.Period,
          thresholds: CommercialThresholds) -> None:
    """Cost of what was sold in the window, or a refusal with its reason.

    The guard is around the *arithmetic*, not around the objection: summing
    ``cogs`` over the costed lines alone is a smaller number that looks like a
    real one, and it would make both DIO and DPO longer than they are. Coverage
    is measured in lines, which is what ``min_cost_coverage`` is defined as.
    """
    lines = [s for s in sold if window.start <= s.date <= window.end]
    if not lines:
        row.why["cogs"] = (
            f"Nothing was sold between {window.start.isoformat()} and "
            f"{window.end.isoformat()}, so there is no cost of sales to divide "
            "by.")
        return

    costed = [s for s in lines if s.cogs is not None]
    coverage = len(costed) / len(lines)
    row.cost_coverage = round(coverage, 4)
    if coverage < thresholds.min_cost_coverage:
        row.why["cogs"] = (
            f"{len(costed)} of {len(lines)} sale lines in the window carry an "
            f"applicable cost record — below the {thresholds.min_cost_coverage:.0%} "
            "this platform treats as enough. Adding up the costed ones alone "
            "would understate cost of sales and stretch both the inventory and "
            "the payable leg.")
        return
    row.cogs = sum((s.cogs for s in costed if s.cogs is not None), _ZERO)


def _inventory(row: Month, observations: dict[date, list[Held]],
               period: periods.Period) -> None:
    """What was on the shelf at the last observation *inside* this month.

    Inside, not "the most recent one before it". Carrying a stale observation
    forward is the substitution the module docstring refuses: it would draw a
    flat inventory leg through months nobody looked at, which reads as stability
    rather than as absence.
    """
    inside = [day for day in observations if period.contains(day)]
    if not inside:
        row.why["dio"] = (
            f"No stock was observed between {period.start.isoformat()} and "
            f"{period.end.isoformat()}. Zoho holds no stock history, so this "
            "month cannot be recovered — and the previous month's shelf is not "
            "a stand-in for it.")
        return

    observed_on = max(inside)
    row.stock_observed_on = observed_on
    lines = observations[observed_on]
    valued = [h for h in lines if h.value is not None]
    row.items_valued = len(valued)
    row.items_unvalued = len(lines) - len(valued)
    if not valued:
        row.why["dio"] = (
            f"{len(lines)} item(s) were observed on {observed_on.isoformat()} "
            "and none of them carries a purchase rate, so the shelf has no "
            "value to divide.")
        return
    row.inventory = sum((h.value for h in valued if h.value is not None), _ZERO)


def _dso(row: Month, reliable_from: Optional[date]) -> None:
    if reliable_from is None or row.window.start < reliable_from:
        row.why["dso"] = (
            "Receivables cannot be reconstructed this far back: "
            + ("no invoice is on record for this book."
               if reliable_from is None else
               f"the window opens on {row.window.start.isoformat()}, before "
               f"{reliable_from.isoformat()}, which is the earliest date "
               "invoices, receipts and credit notes together account for."))
        return
    if row.billed <= _ZERO:
        row.why["dso"] = ("Nothing was billed in the window, so there is no "
                          "revenue to measure the receivable against.")
        return
    if row.receivables is None:
        return
    row.dso = _days(row.receivables, row.billed, row.window_days)


def _dpo(row: Month, reliable_from: Optional[date]) -> None:
    if reliable_from is None or row.window.start < reliable_from:
        row.why["dpo"] = (
            "Payables cannot be reconstructed this far back: "
            + ("no bill is on record for this book."
               if reliable_from is None else
               f"the window opens on {row.window.start.isoformat()}, before "
               f"{reliable_from.isoformat()}, which is the earliest date bills "
               "and payments together account for."))
        return
    if row.cogs is None:
        row.why["dpo"] = row.why.get(
            "cogs", "Cost of sales is unknown for this window.")
        return
    if row.payables is None:
        return
    row.dpo = _days(row.payables, row.cogs, row.window_days)


def _dio(row: Month) -> None:
    if row.inventory is None:
        row.why.setdefault("dio", "The shelf could not be valued for this month.")
        return
    if row.cogs is None:
        row.why["dio"] = row.why.get(
            "cogs", "Cost of sales is unknown for this window.")
        return
    row.dio = _days(row.inventory, row.cogs, row.window_days)


def _observations(held: list[Held]) -> dict[date, list[Held]]:
    out: dict[date, list[Held]] = {}
    for row in held:
        out.setdefault(row.on, []).append(row)
    return out


def _definition(thresholds: CommercialThresholds) -> dict:
    """What each leg is, in the reader's terms.

    Rendered as a legend rather than explained again on the client. A number
    somebody cannot restate in a sentence is a number they will not act on, and
    two explanations of one figure is one explanation too many.
    """
    return {
        "ccc": ("Days between paying for stock and being paid for it. "
                "DIO + DSO − DPO. Lower is better; negative means suppliers "
                "fund the working capital."),
        "dio": ("Days inventory outstanding — the shelf, valued at purchase "
                "rate, over cost of sales. Only stated for a month this "
                "platform actually observed stock in."),
        "dso": ("Days sales outstanding — invoices raised less receipts and "
                "credit notes applied, over what was billed."),
        "dpo": ("Days payable outstanding — bills raised less payments made, "
                "over cost of sales rather than over purchase spend."),
        "window": ("Each point divides a month-end position by the three "
                   "calendar months ending with it. One month alone swings on "
                   "when a single large invoice landed."),
        "cost_coverage": (
            "The share of the window's sale lines with an applicable cost "
            f"record. Below {thresholds.min_cost_coverage:.0%} the cycle is not "
            "stated at all."),
    }


def _basis() -> dict:
    """The two things a reader has to know before comparing these to anybody's.

    Both are stated rather than corrected, because correcting either would mean
    the platform inventing a number. A cycle whose basis is unstated is one that
    gets compared against a published ratio computed on a different basis, and
    the difference gets read as performance.
    """
    return {
        "tax": ("Receivables, payables and billings are gross — a document's "
                "total is what is actually owed, tax included. Cost of sales is "
                "net of input tax, because that is what a bill line costs. The "
                "payable leg therefore divides a gross balance by a net cost "
                "and reads longer than a fully net measure would; the cycle is "
                "understated by the same amount. The alternatives were a gross "
                "purchase-spend denominator, which is a different measure, and "
                "a tax-adjusted cost, which the platform would have to invent."),
        "inventory_valuation": (
            "Stock is valued at the item master's last purchase rate, which is "
            "what Zoho holds. It is not a weighted average cost and this "
            "platform does not compute one."),
    }


def _unavailable(*, held: list[Held]) -> list[dict]:
    """What this view cannot answer, said plainly rather than left blank."""
    out: list[dict] = [{
        "series": "inventory_before_the_first_observation",
        # Not a sync somebody forgot to run. Zoho reports stock as a level now
        # and keeps no history of it, so the months before this platform started
        # writing snapshots down have no source anywhere and never will.
        "kind": absence.PERMANENT,
        "reason": ("Zoho reports stock as a level today and holds no history, "
                   "so inventory days exist only from the platform's first "
                   "stock observation onward. Earlier months cannot be "
                   "backfilled from any source, and the cycle is withheld for "
                   "them rather than drawn with the nearest shelf borrowed."),
    }, {
        "series": "credit_notes_from_suppliers",
        # BUILDABLE and worth building: Zoho holds vendor credits, this pull
        # does not read them. The receivable side has exactly this term and the
        # payable side does not, which is why it is named rather than absorbed.
        "kind": absence.BUILDABLE,
        "reason": ("Credit notes a supplier issued us are not ingested, so the "
                   "payable position is overstated by whatever they come to — "
                   "which lengthens the payable leg and shortens the cycle. The "
                   "receivable side does subtract applied credit; the two are "
                   "not symmetrical yet."),
    }]
    unvalued = sum(1 for h in held if h.value is None)
    if unvalued:
        out.append({
            "series": "inventory_value_of_unrated_items",
            "kind": absence.COLLECTABLE,
            "reason": (f"{unvalued} stock observation(s) are of items with no "
                       "purchase rate in the master, so they are counted out of "
                       "the shelf's value. Inventory days are a floor for those "
                       "months, not a measurement."),
        })
    return out
