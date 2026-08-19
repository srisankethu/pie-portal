"""What the cycle costs: the money standing in it, what it earns, and what
growth would add to it.

``insight/cycle`` answers how *long* a rupee is tied up. This answers how many
rupees, what they returned while they were tied up, and — the question a growth
decision actually turns on — how much more capital the next rupee of revenue
would need at the cycle this business currently runs. Three readings of one
arithmetic over one set of rows, which is why they are one module: they share a
window, a refusal set and a denominator, and splitting them would mean three
places to get the same alignment wrong.

Nothing here re-reads a document. Every position comes from ``cycle.replay`` —
the same reconstruction the cash-cycle screen draws — so every refusal that
module makes is inherited rather than re-implemented.

The two part company in exactly one place, and it is deliberate rather than a
gap. A window below the cost-coverage floor has no cost of sales, so it has no
cycle, no gross profit and no return on anything — but the *balances* are still
known, and capital employed is a sum of balances. It is stated there, and every
figure that needs an earning or a day count beside it is not. Withholding the
one number that survives would be refusing on a dependency it does not have.

---

## Capital employed: receivables + inventory − payables

**Net of payables, and the choice is not neutral.** The other reading —
receivables plus stock, gross — measures the size of the operating footprint,
and it is the one that flatters a business funded by its suppliers. This one
measures the money the entity has to *find*, which is the question an owner
deciding whether to grow is asking.

The deciding argument is consistency with the cycle itself. ``ccc`` is
``DIO + DSO − DPO``: the payable leg is already subtracted, on the stated ground
that supplier credit funds part of the cycle. A capital figure that added the
payable side back would contradict the days figure printed beside it — an entity
with a negative CCC, where suppliers fund everything and more, would still show
large capital employed. Two numbers on one screen disagreeing about whether a
supplier's patience is an asset or a liability is worse than either being
imprecise.

**It is operating capital, and it is not a balance sheet.** PIE ingests
documents, not a ledger: there are no fixed assets in it, no cash, no borrowings
and no equity. So this is receivables plus stock less payables and nothing else,
and it must never be read as capital employed in the audited sense.
``insight/selffunding`` refuses to substitute gross profit for PAT for exactly
this reason and the same discipline applies here — the figure is named for what
it is and the missing halves are listed in ``unavailable`` rather than being
quietly assumed to be zero.

## Return on it: gross profit over the window ÷ average capital over the same
window

``insight/gmroi`` solved this alignment problem and the discipline is copied
rather than re-derived:

1. **The numerator and the denominator span the same days by construction.**
   Gross profit is summed over the window's sale lines; average capital employed
   is the mean of the month-end balances of the months *in that window*. Both
   are read off the same ``cycle.Month`` rows, so they cannot drift apart.
2. **A window missing any of its month-end balances is not averaged over what
   survived.** ``cycle`` refuses a composite whose legs are not all stated —
   "never the sum of the legs that happen to be known" — and an average over the
   two month ends somebody happened to observe is the same mistake wearing a
   mean. The reason names how many points were needed and how many exist.
3. **Nothing is annualised.** The window is a quarter; multiplying by four to
   print an annual return is a projection, and it would print in the same
   typeface as a measurement. What ships is the window figure with the window on
   it.

**The numerator is gross profit, not EBIT, and this is therefore not ROCE.**
There is no opex, no depreciation and no tax in this platform, and inventing
them would overstate the return by the entire cost of running the business — in
the direction that reads as good news, which is the benign default CLAUDE.md
names three times. The number is called *return on operating capital employed*,
it is stated on a gross-profit numerator, and ``unavailable`` says plainly that
it may not be compared with a published ROCE.

**Cost coverage is reported, never smoothed.** Above the coverage floor, gross
profit is summed over the costed lines only, so a window at 70% coverage carries
an understated numerator. ``gmroi``'s answer is followed exactly: say which
lines the figure speaks for rather than picking a different denominator to
compensate. Below the floor there is no COGS, and therefore no gross profit, no
cycle, and nothing here.

## Money in the cycle, and what a collections push releases

``ccc × daily COGS`` is the cycle restated as money, and it sits beside the
direct sum rather than replacing it. The two are the same quantity and they do
not agree: the receivable leg divides a *gross* balance, tax included, by
*billings*, while the other two legs run on cost net of input tax. ``cycle``
states that asymmetry in its own basis note and it is stated again here, because
a reader who spots the gap and is not told why will conclude one of the two is
broken.

The sensitivity is the part that makes a collections push fundable. DSO is
``receivables ÷ billings × days``, so a day off it releases exactly one day of
billings — ``billed ÷ window days`` — and that identity is where the figure
comes from rather than from a rule of thumb. It is **capped at the DSO actually
measured**: offering fifteen days of release on a book collecting in twelve
would be promising cash that is not owed. A capped scenario says so on the row.

## Working capital per rupee of incremental revenue

Average capital employed ÷ revenue over the same window. At the cycle this
business currently runs, a rupee of extra revenue requires that many rupees of
extra capital. It is a *measured intensity*, not a forecast: it assumes the next
rupee behaves like the last ones, and nothing here models a customer paying
differently or stock turning faster.

A negative intensity is a real finding, not an error — an entity whose suppliers
fund more than its receivables and stock together *releases* cash as it grows,
and the sign carries that. A negative **return** is not treated the same way and
the asymmetry is deliberate: dividing gross profit by a negative capital base
produces a number that reads as a loss on a book that is making money, so it is
refused rather than printed.

**Per principal is not computable, and it is not approximated.** Two of the
three legs would attribute cleanly — a bill names its vendor, and stock is
attributable to a principal on the purchase side, which
``commercial/principals`` says must take the vendor off the bill with no
manufacturer fallback. The receivable leg does not. A customer's invoice mixes
several principals' products and a receipt is applied to the *invoice*, not to
its lines, so which principal's rupee has come back cannot be recovered from
anything this platform holds. Splitting a receivable by revenue share would be
an allocation with no evidence behind it, and it would be the largest of the
three legs. So this is computed per entity only, and the refusal is typed and
named rather than being filled in with the fallback ``principals`` exists to
keep off purchase-side numbers.

## Per entity, and no pooled total

Inherited from ``cycle`` without exception. Three legal entities' balances added
together produce a capital figure belonging to no company and matching no
filing, and a return computed on it would divide one book's profit by another
book's stock. There is deliberately no group series and ``unavailable`` says so,
because a missing total with nothing said reads as an omission somebody should
fix.

Layer rules, inherited: this is ``commercial/``, deterministic, and it never
imports ``ai/``. Money is ``Decimal`` throughout — the ratios here are money
divided by money, and a float in the middle of that is a rounding error nobody
can trace. Everything below is gross profit and stock at cost, which makes the
whole module management information; see the endpoint for the scope rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from ..config import CommercialThresholds
from . import absence, cycle, periods
#: What a figure rests on. Imported vocabulary rather than a second one: these
#: are the two words ``gmroi`` already uses for "complete" and "an
#: understatement of known size", and one concept named twice on two screens is
#: how a reader learns that neither name means anything in particular.
from .gmroi import MEASURED, PARTIAL_COST

_ZERO = Decimal(0)

#: The readings a month may or may not carry, named once so the response, the
#: refusal reasons and any table built on them cannot disagree about the set.
#: The same device ``cycle.LEGS`` is, for the same reason.
READINGS = ("capital_employed", "return_on_capital",
            "working_capital_per_rupee", "cash_in_cycle")


@dataclass
class Point:
    """One month end for one book: the capital in the cycle, and what it earned.

    Every optional field is optional for a reason that is written into ``why``.
    None is never a zero here — a month with no observed shelf has *unknown*
    capital employed, and rendering that as nil would report an entity as
    funding its trade out of nothing.
    """

    period: periods.Period
    window: periods.Period
    window_days: int

    #: Straight from the replayed cycle, unrounded.
    receivables: Optional[Decimal]
    payables: Optional[Decimal]
    inventory: Optional[Decimal]
    billed: Decimal
    cogs: Optional[Decimal]
    cost_coverage: Optional[float]
    ccc: Optional[float]
    dso: Optional[float]

    #: Revenue of every sale line in the window, costed or not. This is not the
    #: ``weather`` defect: nothing below divides a costed-subset *profit* by
    #: this to make a margin. It is the denominator of an intensity whose
    #: numerator is a balance rather than an earning, and an incremental rupee
    #: of revenue is an incremental rupee of revenue whether or not a bill was
    #: entered behind it.
    revenue: Decimal
    #: Revenue of the lines a cost record covers — the ones gross profit speaks
    #: for, and the reason the coverage share travels beside it.
    costed_revenue: Decimal
    lines: int
    costed_lines: int

    capital_employed: Optional[Decimal] = None
    gross_profit: Optional[Decimal] = None
    avg_capital_employed: Optional[Decimal] = None
    #: Month ends inside the window that stated a capital figure, out of the
    #: number the window needs. Unequal means the average was refused.
    capital_points: int = 0
    window_points: int = 0
    why: dict[str, str] = field(default_factory=dict)

    # ── the three readings ───────────────────────────────────────────────────
    @property
    def return_on_capital(self) -> Optional[Decimal]:
        """Gross profit per rupee of average capital, over the window.

        Not annualised, and never stated on a nil or negative base. The guard is
        on the arithmetic rather than on the objection: a window whose profit is
        unknown returns nothing at all instead of falling through to a figure
        divided by a denominator that is not there.
        """
        if self.gross_profit is None or self.avg_capital_employed is None:
            return None
        if self.avg_capital_employed <= _ZERO:
            return None
        return self.gross_profit / self.avg_capital_employed

    @property
    def working_capital_per_rupee(self) -> Optional[Decimal]:
        """Capital the cycle requires per rupee of revenue it carried.

        Sign preserved, unlike ``return_on_capital``. A negative base makes a
        return unreadable — profit over a negative denominator prints as a loss
        on a book that is earning — but it makes an intensity *true*: a cycle
        the suppliers over-fund releases cash as it grows, and that is the
        finding, not an error to be suppressed.
        """
        if self.avg_capital_employed is None or self.revenue <= _ZERO:
            return None
        return self.avg_capital_employed / self.revenue

    @property
    def daily_cogs(self) -> Optional[Decimal]:
        if self.cogs is None:
            return None
        return self.cogs / Decimal(self.window_days)

    @property
    def cash_in_cycle(self) -> Optional[Decimal]:
        """``CCC days × daily cost of sales`` — the cycle restated as money.

        Beside ``capital_employed`` rather than instead of it. They are the same
        quantity by two routes and they differ, because the receivable leg
        divides a gross balance by billings while the other two run on cost net
        of input tax. Reporting one alone would hide a basis difference a reader
        is entitled to see.
        """
        daily = self.daily_cogs
        if self.ccc is None or daily is None:
            return None
        return Decimal(str(self.ccc)) * daily

    @property
    def release_per_dso_day(self) -> Optional[Decimal]:
        """Cash released by taking one day off DSO — one day of billings.

        Straight out of the definition of DSO (``receivables ÷ billings ×
        days``) rather than out of a rule of thumb: one day of the leg *is* one
        day of its own denominator.
        """
        if self.billed <= _ZERO:
            return None
        return self.billed / Decimal(self.window_days)

    def dso_releases(self, reductions: Iterable[int]) -> list[dict]:
        """What each policy reduction in DSO would release, capped at the real
        collection period.

        A fifteen-day improvement offered to a book already collecting in twelve
        is cash that was never owed. The cap is applied *and named*, so a reader
        sees a scenario the business has very nearly achieved already rather
        than a rupee figure that cannot arrive.
        """
        per_day = self.release_per_dso_day
        if per_day is None or self.dso is None:
            return []
        out: list[dict] = []
        for days in reductions:
            applied = min(Decimal(days), Decimal(str(self.dso)))
            if applied <= _ZERO:
                continue
            out.append({
                "days": days,
                "days_applied": float(round(applied, 1)),
                "capped": applied < Decimal(days),
                "cash_released": _money(applied * per_day),
            })
        return out

    @property
    def confidence(self) -> str:
        """Whether gross profit speaks for every line in the window."""
        if self.cost_coverage is None or self.cost_coverage >= 1.0:
            return MEASURED
        return PARTIAL_COST

    def to_dict(self, *, reductions: tuple[int, ...]) -> dict:
        return {
            "month": self.period.label,
            "ends_on": self.period.end.isoformat(),
            "capital_employed": _money(self.capital_employed),
            "avg_capital_employed": _money(self.avg_capital_employed),
            "capital_points": self.capital_points,
            "window_points": self.window_points,
            "receivables": _money(self.receivables),
            "inventory": _money(self.inventory),
            "payables": _money(self.payables),
            "gross_profit": _money(self.gross_profit),
            "revenue": _money(self.revenue),
            "costed_revenue": _money(self.costed_revenue),
            "cogs": _money(self.cogs),
            "billed": _money(self.billed),
            "return_on_capital": _ratio(self.return_on_capital),
            "working_capital_per_rupee": _ratio(self.working_capital_per_rupee),
            "cash_in_cycle": _money(self.cash_in_cycle),
            "release_per_dso_day": _money(self.release_per_dso_day),
            "dso_releases": self.dso_releases(reductions),
            "ccc": self.ccc,
            "dso": self.dso,
            "lines": self.lines,
            "costed_lines": self.costed_lines,
            "cost_coverage": self.cost_coverage,
            "confidence": self.confidence,
            "unknown": [name for name in READINGS
                        if getattr(self, name) is None],
            "why": dict(self.why),
            "window": {"start": self.window.start.isoformat(),
                       "end": self.window.end.isoformat(),
                       "days": self.window_days},
        }


def _money(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(round(value, 2))


def _ratio(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(round(value, 4))


# ── the build ────────────────────────────────────────────────────────────────
def build(*, replays: dict[str, cycle.Replay], sold: Iterable[cycle.Sold],
          as_of: date, thresholds: CommercialThresholds,
          months: int = cycle.DEFAULT_MONTHS,
          window_months: int = cycle.WINDOW_MONTHS) -> dict:
    """Capital, its return and the intensity of growth, per connected company.

    ``replays`` is what ``cycle.replay`` returned, and it should carry
    ``window_months - 1`` more month ends than are wanted on screen: the average
    for the earliest reported month needs the two month ends before it, and a
    series cut at the first reported one would refuse its own opening points for
    a reason that is an artefact of where the caller made the cut. The extra
    rows are used and then dropped.

    Takes rows rather than a session, so the arithmetic is a function of its
    inputs and the router keeps the database — the shape ``cycle`` and ``gmroi``
    already use.
    """
    sold_rows = list(sold)
    entities = [
        {"connection_id": connection_id,
         **_one(one, [s for s in sold_rows if s.book == connection_id],
                months=months, window_months=window_months,
                reductions=thresholds.capital_dso_reduction_days)}
        for connection_id, one in replays.items()
    ]
    entities.sort(key=lambda e: str(e["label"]))
    return {
        "as_of": as_of.isoformat(),
        "months": months,
        "window_months": window_months,
        "entities": entities,
        "definition": _definition(thresholds),
        "basis": _basis(),
        "dso_reduction_days": list(thresholds.capital_dso_reduction_days),
        "min_cost_coverage": thresholds.min_cost_coverage,
        "unavailable": _unavailable(),
        "thresholds_version": thresholds.version,
    }


def _one(one: cycle.Replay, sold: list[cycle.Sold], *, months: int,
         window_months: int, reductions: tuple[int, ...]) -> dict:
    """One company's series. Never merged with another's — see the docstring."""
    rows = [_point(month, sold, receivable_from=one.receivable_from,
                   payable_from=one.payable_from) for month in one.months]
    for i, point in enumerate(rows):
        _average(point, rows[max(0, i - window_months + 1): i + 1],
                 window_points=window_months)
    reported = rows[-months:] if 0 < months < len(rows) else rows

    stated = [r for r in reported if r.capital_employed is not None]
    latest = stated[-1] if stated else None
    return {
        "label": one.label,
        "months": [r.to_dict(reductions=reductions) for r in reported],
        # The month a reader acts on, lifted out rather than looked for at the
        # end of an array — the shape ``cycle._render`` already uses. Taken from
        # the *stated* months, so it is the latest month there is an answer for
        # rather than the latest month there is a row for.
        "latest": latest.to_dict(reductions=reductions) if latest else None,
        "months_stated": len(stated),
        "receivables_reliable_from": (one.receivable_from.isoformat()
                                      if one.receivable_from else None),
        "payables_reliable_from": (one.payable_from.isoformat()
                                   if one.payable_from else None),
        "stock_observed_from": (one.observation_days[0].isoformat()
                                if one.observation_days else None),
        "counts": dict(one.counts),
    }


def _point(month: cycle.Month, sold: list[cycle.Sold], *,
           receivable_from: Optional[date],
           payable_from: Optional[date]) -> Point:
    """One month end's capital and earnings, off one replayed cycle row.

    The coverage decision is **not** retaken here. ``cycle._cogs`` has already
    made it — below the floor it leaves ``cogs`` unset, with its reason — and
    this reads that outcome. Two modules each deciding when cost of sales is
    trustworthy is two floors to move, and the one that gets moved is never the
    one that is read.
    """
    lines = [s for s in sold if month.window.contains(s.date)]
    costed = [s for s in lines if s.cogs is not None]
    point = Point(
        period=month.period, window=month.window, window_days=month.window_days,
        receivables=month.receivables, payables=month.payables,
        inventory=month.inventory, billed=month.billed, cogs=month.cogs,
        cost_coverage=month.cost_coverage, ccc=month.ccc, dso=month.dso,
        revenue=sum((s.revenue for s in lines), _ZERO),
        costed_revenue=sum((s.revenue for s in costed), _ZERO),
        lines=len(lines), costed_lines=len(costed))

    _capital(point, month, receivable_from=receivable_from,
             payable_from=payable_from)
    _profit(point, month)
    _from_the_cycle(point, month)
    return point


def _from_the_cycle(point: Point, month: cycle.Month) -> None:
    """Name the cycle's own refusals where they cost a figure here.

    ``cash_in_cycle`` needs the whole cycle and the sensitivity needs the
    receivable leg, and ``cycle`` states neither unless it can. Leaving those
    blanks unexplained would put an absence on the screen with no reason beside
    it, which is the thing every refusal in this package exists to avoid — so
    the leg's own wording is carried over rather than a second sentence written
    about the same gap.
    """
    if month.ccc is None:
        missing = [month.why[leg] for leg in cycle.LEGS
                   if getattr(month, leg) is None and leg in month.why]
        point.why["cash_in_cycle"] = (
            "The cycle is not stated for this month, so there are no days to "
            "price. " + " ".join(missing)).strip()
    if month.dso is None:
        point.why["dso_releases"] = (
            month.why.get("dso", "Days sales outstanding is not stated for this "
                                 "window.")
            + " Without it there is no collection period to improve on, and a "
              "release figure would be against a number this platform has not "
              "measured.")


def _capital(point: Point, month: cycle.Month, *,
             receivable_from: Optional[date],
             payable_from: Optional[date]) -> None:
    """Receivables + inventory − payables, or a refusal naming the missing leg.

    The guard is around the arithmetic, not around the objection. A month whose
    shelf was never observed has *unknown* capital employed; summing the two
    legs that are known would report the business as funding its trade with
    whatever the stock cost left out, which is a smaller and entirely plausible
    number.

    **The receivable and payable legs are refused on the same boundary
    ``cycle`` refuses DSO and DPO on, and that is the whole point of passing
    the two dates in.** ``cycle._position`` returns ``raised - settled``
    unconditionally, so ``Month.receivables`` and ``Month.payables`` are never
    ``None`` — a guard written against ``is None`` here can never fire, and the
    module would state a headline capital figure for exactly the month ends
    ``cycle`` has just declared it cannot reconstruct a balance for. A receipt
    settling an invoice that predates the sync window makes the position
    *negative* rather than absent, which is why this cannot be caught by
    looking at the value: it has to be caught by the date.
    """
    if point.inventory is None:
        point.why["capital_employed"] = month.why.get(
            "dio", "The shelf could not be valued for this month, so the stock "
                   "the cycle is holding is unknown.")
        return
    unreconstructable = _before_reliable(
        month, receivable_from=receivable_from, payable_from=payable_from)
    if unreconstructable is not None:
        point.why["capital_employed"] = unreconstructable
        return
    point.capital_employed = point.receivables + point.inventory - point.payables


def _before_reliable(month: cycle.Month, *, receivable_from: Optional[date],
                     payable_from: Optional[date]) -> Optional[str]:
    """The reason this month end's balances cannot be trusted, or ``None``.

    Both legs, because capital employed nets one against the other: a payable
    position that is missing the bills settled before the window opened is as
    wrong as a receivable one, and in the direction that *understates* what the
    business has tied up. Phrased the way ``cycle._dso``/``_dpo`` phrase it, so
    a reader who has seen the refusal on the cash-cycle screen recognises it
    here rather than reading it as a second, different problem.
    """
    for leg, reliable_from, documents in (
            ("Receivables", receivable_from, "invoices, receipts and credit notes"),
            ("Payables", payable_from, "bills and payments")):
        if reliable_from is None:
            return (f"{leg} cannot be reconstructed for this book: no "
                    f"{'invoice' if leg == 'Receivables' else 'bill'} is on "
                    "record, so there is no balance to hold against the stock.")
        if month.window.start < reliable_from:
            return (f"{leg} cannot be reconstructed this far back: the window "
                    f"opens on {month.window.start.isoformat()}, before "
                    f"{reliable_from.isoformat()}, which is the earliest date "
                    f"{documents} together account for.")
    return None


def _profit(point: Point, month: cycle.Month) -> None:
    """Gross profit over the window: costed revenue less the cycle's own COGS.

    Reusing ``month.cogs`` rather than re-summing cost is what keeps this from
    becoming a second opinion about what a sold line cost. It also means the
    cost-coverage refusal propagates for free: no cost of sales, no gross
    profit, and therefore no return and no capital intensity.
    """
    if month.cogs is None:
        point.why["gross_profit"] = month.why.get(
            "cogs", "Cost of sales is unknown for this window, so gross profit "
                    "cannot be stated.")
        return
    point.gross_profit = point.costed_revenue - month.cogs


def _average(point: Point, window_rows: list[Point], *,
             window_points: int) -> None:
    """Mean capital employed over the month ends the window covers.

    All of them or none. ``cycle`` refuses a composite whose legs are not all
    stated, and an average over the month ends that happened to be observed is
    that mistake wearing a mean: the numerator would span three months of
    trading and the denominator whichever month ends somebody looked at. It is
    also why the mean is of the *closing* balances of the window's own months —
    a reader can check it against the ``capital_employed`` on the rows above
    without leaving the response.
    """
    point.window_points = window_points
    present = [r.capital_employed for r in window_rows
               if r.capital_employed is not None]
    point.capital_points = len(present)
    if len(window_rows) < window_points or len(present) < window_points:
        point.why["avg_capital_employed"] = (
            f"{len(present)} of the {window_points} month ends in "
            f"{point.window.label} carry a capital figure. An average over the "
            "months that happen to have been observed would be divided into "
            "gross profit from all of them, so no return and no capital "
            "intensity are stated for this window.")
        return
    point.avg_capital_employed = sum(present, _ZERO) / Decimal(len(present))
    if point.avg_capital_employed <= _ZERO:
        # Not an infinite return — an undefined one. The same reading
        # ``gmroi.NO_INVENTORY_HELD`` takes of a nil shelf, and the reason the
        # intensity is still stated here while the return is not.
        point.why["return_on_capital"] = (
            "Suppliers are funding more than the receivables and the shelf "
            "together, so there is no capital standing in the cycle to earn a "
            "return on. The capital intensity keeps its sign and says so; a "
            "return divided by a negative base would print as a loss on a book "
            "that is making money.")
    if point.revenue <= _ZERO:
        point.why["working_capital_per_rupee"] = (
            "Nothing was sold in this window, so there is no revenue for the "
            "capital to be measured against.")


# ── what the numbers mean, and what they are not ─────────────────────────────
def _definition(thresholds: CommercialThresholds) -> dict:
    """Each figure in the reader's terms, rendered as a legend.

    Here rather than on the client for the reason ``cycle._definition`` gives: a
    number somebody cannot restate in a sentence is a number they will not act
    on, and two explanations of one figure is one explanation too many.
    """
    return {
        "capital_employed": (
            "Receivables plus stock at cost, less what is owed to suppliers — "
            "the money this entity has standing in its trading cycle at that "
            "month end. Operating capital only: no fixed assets, no cash, no "
            "borrowings."),
        "avg_capital_employed": (
            "The mean of the capital employed at each month end in the window, "
            "so it covers the same months the profit beside it was earned in."),
        "return_on_capital": (
            "Gross profit earned in the window per rupee of average capital "
            "employed over the same window. Not annualised, and not ROCE — the "
            "numerator is gross profit, not operating profit."),
        "working_capital_per_rupee": (
            "Capital the cycle needs per rupee of revenue. At this cycle a "
            "rupee of extra revenue needs this much extra capital; a negative "
            "figure means growth releases cash instead."),
        "cash_in_cycle": (
            "The cycle restated as money: CCC days × daily cost of sales. It "
            "differs from capital employed because the receivable leg is gross "
            "of tax and measured against billings — see the basis note."),
        "release_per_dso_day": (
            "Cash freed by collecting one day faster — one day of billings, "
            "which is what a day of DSO is by definition."),
        "cost_coverage": (
            "The share of the window's sale lines with an applicable cost "
            f"record. Below {thresholds.min_cost_coverage:.0%} nothing here is "
            "stated; above it gross profit speaks for the covered lines and the "
            "row is marked PARTIAL_COST."),
    }


def _basis() -> dict:
    """The things a reader has to know before comparing this to anything.

    Stated rather than corrected, because correcting any of them would mean the
    platform inventing a number — the same position ``cycle._basis`` takes about
    the tax asymmetry this inherits from it.
    """
    return {
        "tax": ("Receivables, payables and billings are gross — a document's "
                "total is what is actually owed. Stock and cost of sales are "
                "net of input tax. Capital employed therefore mixes a gross "
                "receivable with a net shelf, and the money-in-cycle figure "
                "beside it differs from the direct sum for that reason. The "
                "alternative was a tax-adjusted balance the platform would have "
                "to invent."),
        "not_a_balance_sheet": (
            "This is operating working capital, not capital employed in the "
            "audited sense. Fixed assets, cash, borrowings and equity are not "
            "ingested by this platform and are not assumed to be nil."),
        "not_annualised": (
            "Every figure covers the window printed on its row — three calendar "
            "months. Multiplying a quarter's return by four would print a "
            "projection with the authority of a measurement."),
        "inventory_valuation": (
            "Stock is valued at the item master's last purchase rate, which is "
            "what Zoho holds. It is not a weighted average cost and this "
            "platform does not compute one."),
    }


def _unavailable() -> list[dict]:
    """What this view cannot answer, said plainly rather than left blank."""
    return [{
        "series": "return_on_capital_employed_as_published",
        # BUILDABLE rather than PERMANENT, and the distinction is the point: a
        # real ROCE is not blocked by anything unknowable, it is blocked by two
        # ingestions this platform has not done. Filing it under PERMANENT would
        # tell somebody not to bother.
        "kind": absence.BUILDABLE,
        "reason": ("A published ROCE divides operating profit by fixed assets "
                   "plus working capital. This platform ingests documents, not "
                   "a ledger: there is no opex, depreciation or tax in it, and "
                   "no fixed asset register. The numerator here is gross profit "
                   "and the denominator is operating working capital only, so "
                   "the figure is higher than a ROCE by an unknown margin and "
                   "must not be compared with one."),
    }, {
        "series": "working_capital_per_principal",
        # PERMANENT on the grain reading `absence` defines: a payment is applied
        # to an invoice, and no amount of data collection makes an
        # invoice-level receipt into a line-level one.
        "kind": absence.PERMANENT,
        "reason": ("Two of the three legs would attribute: a bill names its "
                   "vendor, and stock attributes to a principal off the "
                   "purchase bill. The receivable does not. One invoice carries "
                   "several principals' products and a receipt is applied to "
                   "the invoice rather than to its lines, so whose rupee has "
                   "come back cannot be recovered. Splitting the largest of the "
                   "three legs by revenue share would be an allocation with no "
                   "evidence behind it, so this is stated per entity only."),
    }, {
        "series": "group_capital_employed",
        "kind": absence.PERMANENT,
        "reason": ("There is deliberately no total across the connected "
                   "companies. Three balance sheets added together produce a "
                   "capital figure belonging to no legal entity and matching no "
                   "filing, and a return computed on it would divide one book's "
                   "profit by another book's stock."),
    }, {
        "series": "capital_before_the_first_stock_observation",
        "kind": absence.PERMANENT,
        "reason": ("Zoho reports stock as a level today and holds no history, "
                   "so capital employed exists only from this platform's first "
                   "stock observation onward. Earlier months cannot be "
                   "backfilled from any source, and nothing derived from them — "
                   "the return, the money in the cycle, the capital a rupee of "
                   "growth needs — is stated for those months either."),
    }, {
        "series": "annualised_return_on_capital",
        "kind": absence.PERMANENT,
        "reason": ("Annualising is not blocked by missing data. Scaling a "
                   "quarter's return to a year is a projection about the three "
                   "quarters that have not happened, and it would print in the "
                   "same typeface as the measurement. The window figure is "
                   "shown with its window on it instead."),
    }, {
        "series": "supplier_credit_notes",
        # BUILDABLE and worth building. Restated from `cycle` rather than
        # referenced: there it lengthens a day count, here it moves a money
        # figure the reader is being asked to fund.
        "kind": absence.BUILDABLE,
        "reason": ("Credit notes a supplier issued us are not ingested, so the "
                   "payable position is overstated by whatever they come to — "
                   "which understates capital employed and overstates the "
                   "return on it. The receivable side does subtract applied "
                   "credit; the two are not symmetrical yet."),
    }]
