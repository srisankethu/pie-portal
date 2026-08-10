"""GMROI — what each line earns for the cash it ties up.

Gross profit over a window ÷ average inventory at cost over the **same** window.
A ₹2.40 answer means every rupee sitting on the shelf in that line returned
₹2.40 of gross profit while it sat there. It is the one figure that sorts a
catalogue into earners and freeloaders, because margin alone rewards a slow
high-margin line that ties up cash for a year and turns alone rewards a fast
line that earns nothing per turn.

**The window is the whole difficulty, and it is not a detail.** Zoho reports
stock as a *current* number and keeps no history, so the denominator only exists
for days something wrote a reading down — ``StockSnapshot``, one row per item per
day. A platform running a month can draw a month; one that started yesterday
draws a point. So a "12-month GMROI" computed on two months of snapshots is not
that number, and the shape of the mistake is a real one: the numerator would come
from a year of invoices, the denominator from eight weeks of shelf readings, and
the ratio would be roughly six times the truth with nothing on the screen saying
so.

Three consequences, and all three are load-bearing:

1. **The effective window is the intersection**, not the request. A caller asks
   for twelve months; what comes back is the span the snapshots actually cover,
   and gross profit is summed over *exactly that span*. Numerator and denominator
   are the same days by construction rather than by a comment asking somebody to
   check.
2. **Below a usable window there is no figure at all.** Under
   ``MIN_OBSERVED_DAYS`` distinct observation days the response carries
   ``measurable: false``, no rows, and the reason — never a number computed from
   four readings.
3. **Nothing is annualised.** Scaling a 47-day figure to a year is a projection,
   and it would print with the same authority as a measurement. What ships is the
   window figure with the window stated on it, per row and in the totals; the
   refusal to annualise is in ``unavailable`` where it will be read.

**Average, over the days actually observed for that item.** An item's average
inventory is the mean of ``on_hand × purchase_rate`` across the days it was seen
and costed — not across every observation day in the window. Dividing by days the
platform never observed the item would be interpolating zeros into the average,
which halves the denominator of anything first stocked mid-window and turns a
normal line into a spectacular earner. ``inventory_days`` travels on every row so
a thin average says how thin it is.

**Three ways this refuses, and only one of them is a zero.**

``gmroi = 0.0``  is a measurement. Nothing sold in the window, so nothing was
                 earned, and that is the finding this whole view exists for.
``gmroi = None`` with ``NO_COSTED_SALE`` is missing evidence: there were sales
                 and no bill behind any of them, so gross profit is unknown. It
                 is emphatically not zero — reading it as zero would band an
                 earner a freeloader on the platform's own ignorance.
``gmroi = None`` with ``NO_INVENTORY_HELD`` is the divide-by-zero. An item with
                 no average inventory has no GMROI, not an infinite one.

**Cost coverage is reported, never smoothed.** Where only some of an item's sales
have a cost record behind them, gross profit is summed over the costed subset —
which understates the numerator — so the row carries ``PARTIAL_COST`` and its
coverage share. That is the ``weather`` defect turned around: the mistake there
was dividing costed profit by *all* revenue, and the fix is not to pick a
different denominator but to say which lines the number speaks for.

**Brands, through the cascade, with the residue as its own line.** Attribution
comes from ``commercial/principals.py`` — bill vendor first, item manufacturer
second — and never from ``Product.manufacturer`` read directly, because that
field is untagged on about a third of the item master. A brand's GMROI is
Σ gross profit ÷ Σ average inventory across its items, never the mean of their
GMROIs. Items nothing could attribute get a line of their own and keep their own
name for it: folded into an "Other" bucket they would read as a small brand
nobody has to think about, and on this book they are not small.

**Scope: SKU and brand.** Branch is a separate dimension and deliberately absent
— stock is read per Zoho company rather than per warehouse, which is the same
limit ``stock.py`` records against its own ``branch`` series.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Everything here is gross profit divided by purchase cost, which makes the whole
module RESTRICTED — see ``withheld()`` for the entry a salesperson gets instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from ...signals.base import CostRow, SaleRow, group_by
from .. import principals
from ..config import CommercialThresholds
from ..economics import LineEconomics, aggregate, in_window, line_economics
from . import absence

_ZERO = Decimal("0")

#: Distinct days of stock observation below which no GMROI is computed at all.
#:
#: Four weeks, and the reason is the denominator rather than the numerator. An
#: average inventory drawn from a handful of readings is dominated by which days
#: happened to be observed — one delivery landing on an observed day and clearing
#: on an unobserved one moves it by the whole consignment. Below four weeks the
#: figure moves more with the sampling than with the business, and a ratio over a
#: fortnight is not comparable to anything anybody would compare it to.
#:
#: A module constant rather than a threshold in Settings, on purpose: it is not
#: commercial policy an owner should be able to lower. It is the point below
#: which the arithmetic stops describing the shelf, and making it editable would
#: make "produce a number for the demo" a supported operation.
MIN_OBSERVED_DAYS = 28

#: What a row's figure rests on.
MEASURED = "MEASURED"
#: Some of the item's sales in the window have no cost record behind them, so
#: gross profit is the costed subset's and the figure is an understatement of
#: known size. The share is on the row.
PARTIAL_COST = "PARTIAL_COST"

#: Why a row has no GMROI. Distinguished because they want different responses:
#: one is a purchase, two are clerical, and one is arithmetic.
NO_INVENTORY_OBSERVED = "NO_INVENTORY_OBSERVED"
NOT_STOCKED = "NOT_STOCKED"
INVENTORY_NOT_COSTED = "INVENTORY_NOT_COSTED"
NO_INVENTORY_HELD = "NO_INVENTORY_HELD"
NO_COSTED_SALE = "NO_COSTED_SALE"

REASON_MEANING: dict[str, str] = {
    NO_INVENTORY_OBSERVED: ("No stock reading was written for this item inside "
                            "the window, so there is no denominator. Snapshots "
                            "start the day the platform first synced; they "
                            "cannot be backfilled."),
    NOT_STOCKED: ("Not stock-tracked in the item master — a service or a "
                  "non-inventory item. It ties up no inventory, so GMROI is "
                  "undefined rather than missing."),
    INVENTORY_NOT_COSTED: ("The item was on the shelf, but no purchase rate "
                           "came with the reading, so the shelf cannot be "
                           "valued. Not treated as free."),
    NO_INVENTORY_HELD: ("Average inventory over the window is nil, so there is "
                        "no investment to return on. Not an infinite GMROI — "
                        "an undefined one."),
    NO_COSTED_SALE: ("This item sold inside the window and no bill covers any "
                     "of it, so gross profit is unknown. Not zero: reading it "
                     "as zero would band an earner a freeloader on the "
                     "platform's own ignorance."),
}

#: How much evidence each attribution source carries, strongest first. A bill is
#: a transaction this book paid; a manufacturer is an attribute somebody typed.
#: Used to decide what a *rolled-up* brand may claim — see ``Brand.source``.
_SOURCE_STRENGTH: dict[str, int] = {
    principals.BY_BILL: 2,
    principals.BY_MANUFACTURER: 1,
    principals.BY_NOTHING: 0,
}

#: The key the unattributed line carries. Never a vendor id and never blank, so
#: nothing downstream can hand it to something that would look up a ``Vendor``.
UNATTRIBUTED = "UNATTRIBUTED"
UNATTRIBUTED_LABEL = "Not attributed to a principal"


@dataclass(frozen=True)
class Observation:
    """One day's stock reading for one item, as ``StockSnapshot`` recorded it.

    ``on_hand`` and ``purchase_rate`` are both optional and independently so: a
    reading with no rate values nothing, and a rate with no reading measures
    nothing. Neither absence is a zero.
    """

    product_id: str
    as_of: date
    on_hand: Optional[Decimal]
    purchase_rate: Optional[Decimal]
    #: False for services and non-inventory items. They have no shelf, so they
    #: are named as undefined rather than counted as holding nothing.
    tracked: bool = True

    @property
    def value(self) -> Optional[Decimal]:
        """What the shelf held that day, at what it cost. ``None`` if unknown."""
        if self.on_hand is None or self.purchase_rate is None:
            return None
        # A zero or negative purchase rate is a placeholder rather than a price,
        # the same reading ``economics.line_economics`` takes of one — valuing a
        # shelf at nothing would make every line on it a free earner.
        if self.purchase_rate <= _ZERO:
            return None
        return self.on_hand * self.purchase_rate


@dataclass(frozen=True)
class Window:
    """The span the figures actually cover, and how it compares to the request.

    Both dates *and* the observation count, because they answer different
    questions. The span says which calendar days the ratio is about; the count
    says how many of them the platform actually looked at. A 90-day span with
    nine readings in it is not a 90-day average, and only the second number
    says so.
    """

    #: What the caller asked for, in days. Kept so the response can say plainly
    #: that it did not deliver it.
    requested_days: int
    #: The effective window — the request clipped to what the snapshots cover.
    start: Optional[date]
    end: Optional[date]
    #: Distinct days carrying at least one stock reading inside it.
    days_observed: int

    @property
    def span_days(self) -> int:
        """Calendar days the effective window covers, inclusive of both ends."""
        if self.start is None or self.end is None:
            return 0
        return (self.end - self.start).days + 1

    @property
    def measurable(self) -> bool:
        return self.days_observed >= MIN_OBSERVED_DAYS

    @property
    def covers_request(self) -> bool:
        return self.span_days >= self.requested_days

    def basis(self) -> str:
        """The sentence a reader needs before they read the ratio."""
        if self.start is None or self.end is None:
            return ("No stock has been observed yet, so there is no window to "
                    "measure over.")
        return (f"Gross profit from {self.start.isoformat()} to "
                f"{self.end.isoformat()} ({self.span_days} days) ÷ average "
                f"inventory at cost over the same {self.span_days} days, from "
                f"{self.days_observed} day(s) of stock readings. Not annualised.")

    def shortfall(self) -> Optional[str]:
        """Why this is not the window that was asked for, or ``None``.

        Said rather than implied. The failure this guards against is a screen
        headed "12 months" over eight weeks of readings, which is not a rounding
        problem — it is a figure roughly six times smaller than the one its
        heading promises.
        """
        if self.start is None:
            return ("Stock history starts the day the platform first synced, and "
                    "none has been written yet. Zoho holds no stock history to "
                    "backfill from, so this fills in as snapshots accumulate.")
        if not self.measurable:
            return (f"Only {self.days_observed} day(s) of stock readings exist, "
                    f"below the {MIN_OBSERVED_DAYS} needed for an average that "
                    "describes the shelf rather than the sampling. GMROI is "
                    "withheld rather than computed from them.")
        if not self.covers_request:
            return (f"{self.requested_days} days were asked for and "
                    f"{self.span_days} are covered: stock readings begin "
                    f"{self.start.isoformat()}. Gross profit is summed over the "
                    "covered span only, so the ratio is that span's — not the "
                    "one requested, and not annualised to it.")
        return None

    def to_dict(self) -> dict:
        return {
            "requested_days": self.requested_days,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "span_days": self.span_days,
            "days_observed": self.days_observed,
            "min_observed_days": MIN_OBSERVED_DAYS,
            "measurable": self.measurable,
            "covers_request": self.covers_request,
            "basis": self.basis(),
            "shortfall": self.shortfall(),
        }


def window_for(observations: Iterable[Observation], *, as_of: date,
               requested_days: int) -> Window:
    """The effective window: the request, clipped to what was actually observed.

    Separate from ``build`` because the clip has to happen before gross profit is
    summed — the whole correctness of the ratio is that both halves run over the
    same days, and computing the numerator first and reconciling afterwards is
    how they come to differ.
    """
    days = sorted({o.as_of for o in observations
                   if o.as_of <= as_of
                   and o.as_of > as_of - timedelta(days=requested_days)})
    if not days:
        return Window(requested_days=requested_days, start=None, end=None,
                      days_observed=0)
    return Window(requested_days=requested_days, start=days[0], end=days[-1],
                  days_observed=len(days))


@dataclass
class Sku:
    """One item's GMROI, and everything needed to check it."""

    product_id: str
    label: str
    gross_profit: Optional[Decimal]
    avg_inventory: Optional[Decimal]
    #: Days of costed stock reading behind ``avg_inventory``.
    inventory_days: int
    revenue: Decimal
    txns: int
    costed_txns: int
    reason: Optional[str]
    principal_id: str
    principal_label: str
    principal_source: str

    @property
    def gmroi(self) -> Optional[Decimal]:
        """Gross profit per rupee of shelf. ``None`` where either half is unknown.

        The guard is on the *arithmetic*, not on the objection — an item with no
        measurable inventory returns nothing rather than falling through to a
        figure computed from a denominator that is not there.
        """
        if self.gross_profit is None or self.avg_inventory is None:
            return None
        if self.avg_inventory <= _ZERO:
            return None
        return self.gross_profit / self.avg_inventory

    @property
    def cost_coverage(self) -> Optional[float]:
        """Share of this item's sale lines in the window that had a cost record."""
        if self.txns == 0:
            return None
        return self.costed_txns / self.txns

    @property
    def confidence(self) -> str:
        coverage = self.cost_coverage
        return MEASURED if coverage is None or coverage >= 1.0 else PARTIAL_COST

    @property
    def measured(self) -> bool:
        return self.gmroi is not None

    def to_dict(self) -> dict:
        gmroi = self.gmroi
        return {
            "product_id": self.product_id,
            "label": self.label,
            "gmroi": (round(float(gmroi), 4) if gmroi is not None else None),
            "gross_profit": (round(float(self.gross_profit), 2)
                             if self.gross_profit is not None else None),
            "avg_inventory_at_cost": (round(float(self.avg_inventory), 2)
                                      if self.avg_inventory is not None else None),
            "inventory_days": self.inventory_days,
            "revenue": round(float(self.revenue), 2),
            "txns": self.txns,
            "costed_txns": self.costed_txns,
            "cost_coverage": (round(self.cost_coverage, 4)
                              if self.cost_coverage is not None else None),
            "confidence": self.confidence,
            "reason": self.reason,
            "reason_meaning": (REASON_MEANING.get(self.reason)
                               if self.reason else None),
            "principal_id": self.principal_id,
            "principal": self.principal_label,
            "principal_source": self.principal_source,
            "principal_source_label": principals.SOURCE_LABEL.get(
                self.principal_source, self.principal_source),
        }


def _avg_inventory(rows: list[Observation]) -> tuple[Optional[Decimal], int,
                                                     Optional[str]]:
    """Average shelf value for one item, the days behind it, and why not.

    Averaged over the days this item was seen *and* costable, never over every
    observation day in the window — see the module docstring. Returns the reason
    rather than a zero wherever the average cannot be formed, because each of the
    three ways it fails wants a different response and a zero says none of them.
    """
    if not rows:
        return None, 0, NO_INVENTORY_OBSERVED
    if not any(o.tracked for o in rows):
        return None, 0, NOT_STOCKED
    # One reading per day: the table is upserted per item per day, but a caller
    # assembling observations by hand should not be able to double-weight a day.
    per_day: dict[date, Decimal] = {}
    for o in sorted(rows, key=lambda r: r.as_of):
        value = o.value
        if value is not None:
            per_day[o.as_of] = value
    if not per_day:
        return None, 0, INVENTORY_NOT_COSTED
    total = sum(per_day.values(), _ZERO)
    average = total / Decimal(len(per_day))
    if average <= _ZERO:
        # Nil or negative — an oversold shelf reads negative in Zoho's own
        # numbers. There is no investment to return on either way.
        return average, len(per_day), NO_INVENTORY_HELD
    return average, len(per_day), None


@dataclass
class Brand:
    """One principal's GMROI, rolled up from its items.

    Σ gross profit ÷ Σ average inventory — never the mean of the items' ratios,
    which would let a ₹900 line weigh as much as a ₹9 lakh one and is the single
    most common way a rolled-up ratio goes wrong.
    """

    principal_id: str
    label: str
    #: The **weakest** fact placing any of this principal's items, not the first
    #: one encountered. One principal can be reached both ways — a bill names the
    #: vendor for an item bought inside the sync window, and a matched
    #: manufacturer names the same vendor for one bought outside it — so a brand
    #: labelled "from a purchase bill" because its first item happened to be
    #: would be overstating the evidence behind the other seven. The same rule
    #: ``landscape._by_product`` applies to sufficiency: an aggregate is only as
    #: trustworthy as the thinnest row inside it.
    source: str
    #: How many of its items a purchase bill placed. The mix, so a reader can see
    #: how much of the brand the stronger fact actually covers rather than
    #: inferring it from one label.
    skus_from_bill: int
    gross_profit: Decimal
    avg_inventory: Decimal
    skus: int
    skus_measured: int
    #: Gross profit on items the ratio could not include — known earnings with no
    #: measurable shelf behind them. Reported so the reader can see the size of
    #: what was left out instead of it silently depressing the brand's figure.
    gross_profit_excluded: Decimal
    partial_cost_skus: int

    @property
    def attributed(self) -> bool:
        return self.principal_id != UNATTRIBUTED

    @property
    def gmroi(self) -> Optional[Decimal]:
        if self.skus_measured == 0 or self.avg_inventory <= _ZERO:
            return None
        return self.gross_profit / self.avg_inventory

    def to_dict(self) -> dict:
        gmroi = self.gmroi
        return {
            "principal_id": self.principal_id,
            "label": self.label,
            "source": self.source,
            "source_label": principals.SOURCE_LABEL.get(self.source, self.source),
            "skus_from_bill": self.skus_from_bill,
            "attributed": self.attributed,
            "gmroi": (round(float(gmroi), 4) if gmroi is not None else None),
            "gross_profit": round(float(self.gross_profit), 2),
            "avg_inventory_at_cost": round(float(self.avg_inventory), 2),
            "skus": self.skus,
            "skus_measured": self.skus_measured,
            "gross_profit_excluded": round(float(self.gross_profit_excluded), 2),
            "partial_cost_skus": self.partial_cost_skus,
        }


def _roll_up(skus: list[Sku]) -> list[Brand]:
    """Items to principals, with the unattributed residue kept as its own line.

    Requirement, not preference: on a live master here roughly a third of items
    carry no manufacturer and the bill-derived vendor only reaches back as far as
    the sync window, so the residue is large. Folded into an "Other" bucket it
    would read as a small brand; kept apart it reads as what it is — the part of
    the catalogue nothing can place, sized in money.
    """
    acc: dict[str, Brand] = {}
    for sku in skus:
        key = sku.principal_id or UNATTRIBUTED
        label = sku.principal_label or UNATTRIBUTED_LABEL
        brand = acc.get(key)
        if brand is None:
            brand = Brand(principal_id=key, label=label,
                          source=sku.principal_source, skus_from_bill=0,
                          gross_profit=_ZERO,
                          avg_inventory=_ZERO, skus=0, skus_measured=0,
                          gross_profit_excluded=_ZERO, partial_cost_skus=0)
            acc[key] = brand
        brand.skus += 1
        if sku.principal_source == principals.BY_BILL:
            brand.skus_from_bill += 1
        elif _SOURCE_STRENGTH.get(sku.principal_source, 0) < \
                _SOURCE_STRENGTH.get(brand.source, 0):
            # Weakest wins, so the label never claims more evidence than the
            # thinnest item in the roll-up has behind it.
            brand.source = sku.principal_source
        if sku.measured:
            brand.skus_measured += 1
            # `measured` is exactly the pair of guards `gmroi` applies, so both
            # halves are known and positive here.
            brand.gross_profit += sku.gross_profit or _ZERO
            brand.avg_inventory += sku.avg_inventory or _ZERO
            if sku.confidence == PARTIAL_COST:
                brand.partial_cost_skus += 1
        elif sku.gross_profit is not None:
            brand.gross_profit_excluded += sku.gross_profit
    return list(acc.values())


def build(*, sales: Iterable[SaleRow], costs: Iterable[CostRow],
          observations: Iterable[Observation],
          labels: dict[str, str],
          attribution: dict[str, principals.Principal],
          as_of: date, requested_days: int,
          thresholds: CommercialThresholds) -> dict:
    """Every item's and every principal's GMROI, over the window actually covered.

    Takes rows rather than a session so the arithmetic is a function of its
    inputs and the router keeps the database — the shape ``stock`` and ``mix``
    already use.

    ``costs`` is every cost line for the items in play, any order; it is grouped
    and sorted here so a caller cannot hand over an unsorted history and get a
    silently wrong cost basis, which is what ``cost_basis_asof`` would do.
    """
    observed = list(observations)
    window = window_for(observed, as_of=as_of, requested_days=requested_days)

    if not window.measurable:
        # No rows at all, not rows with nulls in them. A grid of blanks invites
        # somebody to fix the blanks; a stated refusal names what is missing.
        return {
            "window": window.to_dict(),
            "measurable": False,
            "skus": [], "brands": [], "totals": {}, "counts": {},
            "reason_meanings": REASON_MEANING,
            "unavailable": _unavailable(window, partial_cost_skus=0,
                                        uncosted_shelf_skus=0),
            "thresholds_version": thresholds.version,
        }

    by_product_costs = {
        pid: sorted(rows, key=lambda c: c.date)
        for pid, rows in group_by(list(costs), lambda c: c.product_id).items()
    }
    obs_by_product = group_by(
        [o for o in observed
         if window.start is not None and window.start <= o.as_of <= window.end],
        lambda o: o.product_id)

    # `in_window` is half-open on the left (`date > start`), which is what makes
    # two adjacent windows partition a range. The effective window is inclusive
    # of its first day, so the bound handed over is the day before it.
    lower = window.start - timedelta(days=1)

    lines_by_product: dict[str, list[LineEconomics]] = {}
    for sale in sales:
        costed = line_economics(sale, by_product_costs.get(sale.product_id, []))
        lines_by_product.setdefault(sale.product_id, []).append(costed)

    skus: list[Sku] = []
    for product_id in sorted(set(obs_by_product) | set(lines_by_product)):
        observations_for = obs_by_product.get(product_id, [])
        period = aggregate(in_window(lines_by_product.get(product_id, []),
                                     lower, window.end))
        if not observations_for and period.txn_count == 0:
            # Neither on the shelf nor traded inside the window. Its only claim
            # on this grid is history outside the window, and a row saying "not
            # measurable" about an item the window has nothing to say about is
            # noise that pushes the rows with a finding off the first page.
            # An item that *sold* here without a reading is a different matter
            # and keeps its row — it earned money off a shelf nothing observed,
            # which is a gap worth naming.
            continue
        average, days, reason = _avg_inventory(observations_for)
        if period.txn_count == 0:
            # Nothing sold while it sat there. A measured zero, and the finding
            # this view exists for — not missing evidence.
            gross_profit: Optional[Decimal] = _ZERO
        else:
            # `aggregate` returns None where no line in the window had a cost
            # record. That is unknown, and it stays unknown.
            gross_profit = period.gross_profit
        if gross_profit is None and reason is None:
            reason = NO_COSTED_SALE

        principal = attribution.get(product_id)
        skus.append(Sku(
            product_id=product_id,
            label=labels.get(product_id) or f"Unnamed item (id {product_id})",
            gross_profit=gross_profit,
            avg_inventory=average,
            inventory_days=days,
            revenue=period.revenue,
            txns=period.txn_count,
            costed_txns=period.costed_txn_count,
            reason=reason,
            principal_id=(principal.principal_id
                          if principal is not None and principal.known
                          else UNATTRIBUTED),
            principal_label=(principal.name
                             if principal is not None and principal.known
                             else UNATTRIBUTED_LABEL),
            principal_source=(principal.source if principal is not None
                              else principals.BY_NOTHING),
        ))

    # Earners first. Rows with no figure sort last rather than as zero — an item
    # nobody can measure is unknown, and putting it at the bottom of a
    # worst-first list is exactly where it must not silently sit.
    skus.sort(key=lambda s: (s.gmroi is None, -(s.gmroi or _ZERO)))
    brands = _roll_up(skus)
    # Attributed principals first, then by what they return. The unattributed
    # line goes last by construction rather than by its size, so it never reads
    # as the worst-performing brand on the page.
    brands.sort(key=lambda b: (not b.attributed, b.gmroi is None,
                               -(b.gmroi or _ZERO)))

    measured = [s for s in skus if s.measured]
    partial = [s for s in measured if s.confidence == PARTIAL_COST]
    uncosted_shelf = [s for s in skus if s.reason == INVENTORY_NOT_COSTED]
    total_profit = sum((s.gross_profit or _ZERO for s in measured), _ZERO)
    total_inventory = sum((s.avg_inventory or _ZERO for s in measured), _ZERO)

    return {
        "window": window.to_dict(),
        "measurable": True,
        "skus": [s.to_dict() for s in skus],
        "brands": [b.to_dict() for b in brands],
        "totals": {
            # The book's own GMROI, on the same rule as a brand's: Σ profit ÷
            # Σ shelf, over the items where both are known.
            "gmroi": (round(float(total_profit / total_inventory), 4)
                      if total_inventory > _ZERO else None),
            "gross_profit": round(float(total_profit), 2),
            "avg_inventory_at_cost": round(float(total_inventory), 2),
        },
        "counts": {
            "skus": len(skus),
            "skus_measured": len(measured),
            "brands": sum(1 for b in brands if b.attributed),
            "unattributed_skus": sum(1 for s in skus
                                     if s.principal_id == UNATTRIBUTED),
            "partial_cost_skus": len(partial),
            "uncosted_shelf_skus": len(uncosted_shelf),
        },
        "reason_meanings": REASON_MEANING,
        "unavailable": _unavailable(window, partial_cost_skus=len(partial),
                                    uncosted_shelf_skus=len(uncosted_shelf)),
        "thresholds_version": thresholds.version,
    }


def _unavailable(window: Window, *, partial_cost_skus: int,
                 uncosted_shelf_skus: int) -> list[dict]:
    out: list[dict] = []
    shortfall = window.shortfall()
    if shortfall:
        out.append({
            "series": "gmroi_over_the_requested_window",
            # TRANSIENT rather than COLLECTABLE: nobody can record a stock level
            # for a day that has already passed, and every day the sync runs the
            # window grows on its own. Putting "wait" on a worklist is noise.
            "kind": absence.TRANSIENT,
            "reason": shortfall,
        })
    out.append({
        "series": "annualised_gmroi",
        # PERMANENT on the honest reading. Annualising is not blocked by missing
        # data — it is blocked by the fact that scaling a partial window to a
        # year is a projection, and this module does not print projections.
        "kind": absence.PERMANENT,
        "reason": ("An annual GMROI needs a year of stock readings or a "
                   "projection from fewer. Zoho keeps no stock history, so the "
                   f"year cannot be reconstructed, and scaling {window.span_days} "
                   "days up to one would print as a measurement without being "
                   "one. The window figure is shown with its window on it "
                   "instead."),
    })
    if partial_cost_skus:
        out.append({
            "series": "gross_profit_on_uncosted_sales",
            # COLLECTABLE: each of these is a bill that has not been entered or
            # not been synced, and somebody can close the gap.
            "kind": absence.COLLECTABLE,
            "reason": (f"{partial_cost_skus} item(s) sold inside the window with "
                       "only some of their lines covered by a bill. Gross profit "
                       "is summed over the covered lines, so their GMROI is an "
                       "understatement — they are marked PARTIAL_COST with the "
                       "covered share on the row rather than being ranked as "
                       "though the figure were complete."),
        })
    if uncosted_shelf_skus:
        out.append({
            "series": "average_inventory_for_unpriced_items",
            "kind": absence.COLLECTABLE,
            "reason": (f"{uncosted_shelf_skus} item(s) were on the shelf inside "
                       "the window with no purchase rate on the reading, so the "
                       "shelf cannot be valued. They carry no GMROI rather than "
                       "a large one computed against a free shelf."),
        })
    out.append({
        "series": "branch",
        # BUILDABLE, and it is the same limit `stock.py` records: the endpoint
        # exists on a Zoho plan this pull does not read.
        "kind": absence.BUILDABLE,
        "reason": ("GMROI by branch needs stock per warehouse. Zoho reports "
                   "location-level stock only on the Inventory plan's warehouse "
                   "endpoints, which this pull does not read, so every "
                   "connected company is effectively one location."),
    })
    return out


def withheld() -> dict:
    """What a reader who cannot see cost is told instead of shown a blank column.

    GMROI is gross profit ÷ purchase cost, so there is nothing left of it once
    the restricted halves are removed and the endpoint refuses outright. That
    leaves a hole on the one screen a salesperson does read about the shelf, and
    a column that vanishes without explanation reads as a bug — the next person
    to notice files one, or puts it back.

    ``WITHHELD`` rather than ``PERMANENT``: the figure is computed and correct
    and a manager sees it. Filing a working permission rule under "not
    answerable" is how it gets "fixed".
    """
    return {
        "series": "gmroi",
        "kind": absence.WITHHELD,
        "reason": ("What each line returns on the cash it ties up is gross "
                   "profit divided by what the stock cost, so it is management "
                   "information end to end — there is no version of it with the "
                   "economics taken out. What a line costs you to keep each "
                   "month is the number this screen is for, and it is on every "
                   "row."),
    }
