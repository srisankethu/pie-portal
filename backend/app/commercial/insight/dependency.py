"""What this book leans on, in both directions.

A distributor sits between principals and customers and is exposed at both ends,
in ways that are the same shape and are almost never looked at together. Lose a
principal and the customers who buy their product are exposed too. Lose the
customer who takes most of a principal's line and the target goes with them.
This module measures both ends the same way so one screen can show them facing
each other.

**The number that matters for a vendor is not spend.** Spend says what a
principal costs; what a *dependency* costs is the revenue riding on their
product. "Kennametal is 12% of our purchasing and 34% of our revenue" is the
sentence — and the second half of it is the one that decides how hard you work
the relationship. Both are computed, both are shown, and the gap between them is
the point.

**Attribution runs through the product.** A sale is attributed to a principal by
the item's dominant supplier — the vendor this book has spent the most with on
that item. That is a real inference and it is stated as one: an item bought from
two suppliers is attributed wholly to the larger, and the response reports how
much of revenue could be attributed at all so a reader is never given a share
computed over a fraction of the book without knowing it.

**There is a third side, and it is the same shape again.** A customer's weight
in revenue and their weight in the *outstanding* book are two different facts,
and they come apart exactly where it matters: a large customer who pays on the
day is a big share of what we sell and a small share of what we are waiting on.
``receivables`` measures that half against the same ``concentration``, so "the
largest five" means one set of names on both figures and a screen can put them
beside each other. Neither is a proxy for the other and neither may be printed
as the other — ``state/opportunities/receivables.py`` makes the same point about
its per-customer card.

**Sole source is a different question from concentration**, and the state layer
already draws that line — see ``state/opportunities/supplier.py``. Nothing here
re-derives it; the count travels through so the two screens agree.

**What cannot be known, and is not implied.** This measures *our* dependency on
a customer. It cannot measure theirs on us: the platform sees what they buy
here and has no sight of what they buy elsewhere, so a customer who gives us
20% of a large spend and one who gives us all of a small one look identical.
That asymmetry is reported rather than papered over — it is the difference
between "they are 8% of our revenue" (a fact) and "we are 8% of their
purchasing" (a guess this platform is not entitled to make).

**That limit still holds, and ``insight/wallet.py`` does not overturn it.** What
that module adds is the narrow set of cases where the other side *is* observable
— a published tender states the quantity being bought, and a quote recorded as
lost to a named competitor is spend this book watched go elsewhere — and it
reports those as a band with the basis named, or refuses. It never produces the
figure this paragraph refuses. Read the two together: this module says what a
customer is worth to us, that one says how much of them we might not have, and
neither is allowed to be stated as the other.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
The vendor half is denominated in purchase spend, so the router — not this
module — decides who may read it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from ..categories import LABELS, UNCATEGORISED
from ..config import CommercialThresholds
from . import absence
from .payments import MIN_SETTLEMENTS, Lag

#: Which end of the book a row describes.
VENDOR = "vendor"
CUSTOMER = "customer"
#: The same customers, weighed by what they still owe rather than by what they
#: bought. A third side rather than a variant of ``CUSTOMER``, because the two
#: are different facts about one name and a screen must never read one as the
#: other — see ``receivables``.
RECEIVABLE = "receivable"

#: What a target is measured against.
ON_PURCHASE = "PURCHASE"
ON_SALES = "SALES"

BASIS_LABEL: dict[str, str] = {
    ON_PURCHASE: "on what we buy from them",
    ON_SALES: "on what we sell of theirs",
}

#: How many names the concentration headline reports a combined share for.
TOP_N = 5


@dataclass(frozen=True)
class Flow:
    """One sale line, attributed to both ends of the book."""

    customer_id: str
    product_id: str
    date: date
    revenue: float
    #: The principal whose product this is, where one could be attributed.
    #: ``None`` is a real and common state — a product this book has never
    #: bought under a bill that named its supplier — and it is counted as
    #: unattributed rather than pushed into an "unknown" vendor that would
    #: accumulate revenue and win the concentration headline outright. The
    #: supplier reducer records the same refusal for spend.
    vendor_id: Optional[str] = None
    category: Optional[str] = None


@dataclass(frozen=True)
class Spend:
    """One bill line, at the grain the supplier state already folds."""

    vendor_id: str
    product_id: str
    date: date
    amount: float


@dataclass(frozen=True)
class Owing:
    """What one customer still owes, at the grain the receivables fold holds.

    ``Decimal``, unlike ``Flow.revenue`` and ``Spend.amount`` beside it. Those
    predate the money rule in the working agreement — ``schemes.py`` says the
    same of ``Target`` — and this is new money, so it arrives as ``Decimal`` and
    is narrowed to ``float`` exactly once, where it feeds the ``float``
    arithmetic ``concentration`` already does for the other two sides. Widening
    the whole module is a change to revenue and spend, which this is not.
    """

    customer_id: str
    outstanding: Decimal


@dataclass(frozen=True)
class Target:
    """What a principal expects, over an explicit period."""

    vendor_id: str
    period_start: date
    period_end: date
    basis: str
    amount: float
    #: The row this came from, where a caller needs to reach what hangs off it —
    #: today, the rebate scheme in ``schemes.py``. Optional because nothing in
    #: *this* module needs an identity: pace and progress are arithmetic on the
    #: dates and the amount, and a required id would make every test construct
    #: one that means nothing.
    target_id: Optional[str] = None

    def covers(self, day: date) -> bool:
        return self.period_start <= day <= self.period_end

    @property
    def days(self) -> int:
        return max(1, (self.period_end - self.period_start).days + 1)

    def elapsed(self, as_of: date) -> int:
        """Days of the period gone by ``as_of``, clamped into it."""
        if as_of < self.period_start:
            return 0
        return min(self.days, (min(as_of, self.period_end)
                               - self.period_start).days + 1)


@dataclass
class Standing:
    """One counterparty's weight in this book, on whichever side it sits."""

    entity_id: str
    label: str
    side: str
    money: float
    share: Optional[float]
    #: Revenue riding on this principal's product. Vendor side only — on the
    #: customer side ``money`` already is revenue and this would restate it.
    downstream_revenue: Optional[float] = None
    downstream_share: Optional[float] = None
    counterparties: int = 0
    sole_source_items: int = 0
    lines: list[str] = field(default_factory=list)
    target: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id, "label": self.label, "side": self.side,
            "money": round(self.money, 2),
            "share": round(self.share, 4) if self.share is not None else None,
            "downstream_revenue": (round(self.downstream_revenue, 2)
                                   if self.downstream_revenue is not None else None),
            "downstream_share": (round(self.downstream_share, 4)
                                 if self.downstream_share is not None else None),
            "counterparties": self.counterparties,
            "sole_source_items": self.sole_source_items,
            "lines": [{"category": c, "label": LABELS.get(c, c)} for c in self.lines],
            "target": self.target,
        }


def build(flows: Iterable[Flow], spends: Iterable[Spend], as_of: date, *,
          thresholds: CommercialThresholds,
          vendor_names: dict[str, str],
          customer_names: dict[str, str],
          targets: Optional[Iterable[Target]] = None,
          sole_source: Optional[dict[str, int]] = None,
          with_suppliers: bool = True) -> dict:
    """Both ends of the book, measured the same way."""
    sales = list(flows)
    bills = list(spends)
    revenue_total = sum(f.revenue for f in sales)

    customers = _customers(sales, customer_names, revenue_total)
    vendors = (_vendors(sales, bills, as_of, vendor_names,
                        revenue_total=revenue_total,
                        targets=list(targets or []),
                        sole_source=sole_source or {},
                        thresholds=thresholds)
               if with_suppliers else None)

    attributed = sum(f.revenue for f in sales if f.vendor_id)
    return {
        "as_of": as_of.isoformat(),
        "customers": {
            "rows": [s.to_dict() for s in customers],
            "total": round(revenue_total, 2),
            "concentration": concentration(customers),
        },
        "vendors": (None if vendors is None else {
            "rows": [s.to_dict() for s in vendors],
            "total": round(sum(s.money for s in vendors), 2),
            "concentration": concentration(vendors),
        }),
        "attribution": {
            "revenue_attributed": round(attributed, 2),
            "revenue_total": round(revenue_total, 2),
            "share": (round(attributed / revenue_total, 4)
                      if revenue_total else None),
        },
        "top_n": TOP_N,
        "basis_labels": BASIS_LABEL,
        "unavailable": unavailable(),
        "thresholds_version": thresholds.version,
    }


def _customers(sales: list[Flow], names: dict[str, str],
               total: float) -> list[Standing]:
    """Our exposure to each customer. Revenue, and how wide they buy."""
    by_customer: dict[str, list[Flow]] = {}
    for f in sales:
        by_customer.setdefault(f.customer_id, []).append(f)

    out: list[Standing] = []
    for customer_id, lines in by_customer.items():
        revenue = sum(f.revenue for f in lines)
        out.append(Standing(
            entity_id=customer_id,
            label=names.get(customer_id) or f"Unnamed customer (id {customer_id})",
            side=CUSTOMER,
            money=revenue,
            share=(revenue / total) if total else None,
            # How many principals their spend runs through. A customer buying
            # across five principals is a different kind of account from one
            # taking a single brand, and it is the mirror of the vendor row's
            # customer count.
            counterparties=len({f.vendor_id for f in lines if f.vendor_id}),
            lines=sorted({f.category for f in lines
                          if f.category and f.category != UNCATEGORISED}),
        ))
    out.sort(key=lambda s: -s.money)
    return out


def _vendors(sales: list[Flow], bills: list[Spend], as_of: date,
             names: dict[str, str], *, revenue_total: float,
             targets: list[Target], sole_source: dict[str, int],
             thresholds: CommercialThresholds) -> list[Standing]:
    """Our exposure to each principal — spend, and the revenue riding on them."""
    spend_by_vendor: dict[str, float] = {}
    for b in bills:
        spend_by_vendor[b.vendor_id] = spend_by_vendor.get(b.vendor_id, 0.0) + b.amount
    spend_total = sum(spend_by_vendor.values())

    downstream: dict[str, float] = {}
    reach: dict[str, set[str]] = {}
    lines: dict[str, set[str]] = {}
    for f in sales:
        if not f.vendor_id:
            continue
        downstream[f.vendor_id] = downstream.get(f.vendor_id, 0.0) + f.revenue
        reach.setdefault(f.vendor_id, set()).add(f.customer_id)
        if f.category and f.category != UNCATEGORISED:
            lines.setdefault(f.vendor_id, set()).add(f.category)

    out: list[Standing] = []
    for vendor_id in set(spend_by_vendor) | set(downstream):
        spend = spend_by_vendor.get(vendor_id, 0.0)
        revenue = downstream.get(vendor_id, 0.0)
        out.append(Standing(
            entity_id=vendor_id,
            label=names.get(vendor_id) or f"Unnamed supplier (id {vendor_id})",
            side=VENDOR,
            money=spend,
            share=(spend / spend_total) if spend_total else None,
            downstream_revenue=revenue,
            downstream_share=(revenue / revenue_total) if revenue_total else None,
            counterparties=len(reach.get(vendor_id, set())),
            sole_source_items=sole_source.get(vendor_id, 0),
            lines=sorted(lines.get(vendor_id, set())),
            target=progress_of(vendor_id, targets, as_of,
                               purchased=spend_in_period(bills, vendor_id, targets, as_of),
                               sold=sales_in_period(sales, vendor_id, targets, as_of)),
        ))
    out.sort(key=lambda s: -(s.downstream_revenue or 0.0))
    return out


def current_target(vendor_id: str, targets: list[Target],
                   as_of: date) -> Optional[Target]:
    """The target period ``as_of`` falls inside, if there is one.

    The *shortest* covering period wins where several overlap: a principal that
    sets a quarter inside an annual number means the quarter to be the live one,
    and reporting progress against the year while somebody is chasing the
    quarter is a screen that answers the wrong question.
    """
    covering = [t for t in targets if t.vendor_id == vendor_id and t.covers(as_of)]
    return min(covering, key=lambda t: t.days) if covering else None


def spend_in_period(bills: list[Spend], vendor_id: str, targets: list[Target],
                    as_of: date) -> float:
    t = current_target(vendor_id, targets, as_of)
    if t is None:
        return 0.0
    return sum(b.amount for b in bills
               if b.vendor_id == vendor_id and t.covers(b.date))


def sales_in_period(sales: list[Flow], vendor_id: str, targets: list[Target],
                    as_of: date) -> float:
    t = current_target(vendor_id, targets, as_of)
    if t is None:
        return 0.0
    return sum(f.revenue for f in sales
               if f.vendor_id == vendor_id and t.covers(f.date))


def progress_of(vendor_id: str, targets: list[Target], as_of: date, *,
                purchased: float, sold: float) -> Optional[dict]:
    """Where this principal's number stands, and whether the pace clears it.

    ``pace`` is the honest half. A quarter that is 80% through with 60% of the
    number done is behind, and an achievement percentage on its own hides that
    until the last week — so the share of the period elapsed travels with it and
    the screen compares the two rather than reporting one.

    Nothing here forecasts. ``required_run_rate`` is arithmetic on what is left
    and what remains of the period; it is not a claim about what will happen.
    """
    target = current_target(vendor_id, targets, as_of)
    if target is None:
        return None
    actual = purchased if target.basis == ON_PURCHASE else sold
    amount = float(target.amount)
    elapsed = target.elapsed(as_of)
    remaining_days = max(0, target.days - elapsed)
    gap = amount - actual
    return {
        "amount": round(amount, 2),
        "actual": round(actual, 2),
        "basis": target.basis,
        "basis_label": BASIS_LABEL.get(target.basis, target.basis),
        "period_start": target.period_start.isoformat(),
        "period_end": target.period_end.isoformat(),
        "achieved": round(actual / amount, 4) if amount else None,
        "period_elapsed": round(elapsed / target.days, 4),
        # Ahead when more of the number is done than of the period. Stated as a
        # comparison rather than a verdict: a lumpy line that always lands in
        # month three is "behind" all quarter and hits every time.
        "on_pace": ((actual / amount) >= (elapsed / target.days)) if amount else None,
        "gap": round(gap, 2),
        "days_left": remaining_days,
        "required_run_rate": (round(gap / remaining_days, 2)
                              if remaining_days > 0 and gap > 0 else None),
    }


def concentration(rows: list[Standing]) -> dict:
    """How much of this side sits with the largest few.

    Shares of a *total*, so they are only ever true of the exact set of rows
    they were computed from. That is why this screen scopes to a company on the
    **server** rather than filtering rows in the browser: these figures are the
    output, and a narrowed list under an org-wide share would put one company's
    rows beneath three companies' arithmetic. ``CompanyFilter`` — which hides
    rows and deliberately never restates a total — is the right control for a
    directory and the wrong one here, for exactly this reason.

    Public because three folds now ask it: revenue, purchase spend and the
    outstanding receivables book. The arithmetic is the same on all three — rank
    by money, share of the total, combined share of the largest ``TOP_N`` — and
    a second copy of it would be how two screens start disagreeing about who the
    largest five are.

    ``top_n_ids`` names those five. Any figure a caller computes *about* the top
    five has to be computed over the same set the share describes, and handing
    back the ids is what makes that impossible to get wrong — a caller that
    re-ranked the rows itself would be one tie-break away from a second answer.
    """
    total = sum(r.money for r in rows)
    if not rows or not total:
        return {"top_share": None, "top_label": None, "top_n_share": None,
                "count": len(rows), "top_n_ids": []}
    ranked = sorted(rows, key=lambda r: -r.money)
    return {
        "top_share": round(ranked[0].money / total, 4),
        "top_label": ranked[0].label,
        "top_n_share": round(sum(r.money for r in ranked[:TOP_N]) / total, 4),
        "count": len(rows),
        "top_n_ids": [r.entity_id for r in ranked[:TOP_N]],
    }


# ── the third side: what is still owed ──────────────────────────────────────
#
# The two lists above weigh a customer by what they bought. This one weighs the
# same names by what they have not yet paid for, and **the two diverge** — a
# large customer who settles promptly is a big share of revenue and a small
# share of the outstanding book, and a smaller one who sits on every invoice is
# the reverse. ``state/opportunities/receivables.py`` says the same thing in
# prose about its per-customer card; this is the stated top-five figure that
# card was never meant to be, and neither is a proxy for the other. They are
# returned together so a screen can put them side by side, because the *gap*
# between them is the finding — the same reason the vendor row draws spend and
# downstream revenue on one track.
#
# What is deliberately not here is a days-sales-outstanding ratio. See
# ``unavailable`` and the reducer's own docstring.


def receivables(owings: Iterable[Owing], *, names: dict[str, str],
                lags: dict[str, Lag]) -> dict:
    """The outstanding book, concentrated, and how long that money has been out.

    Two figures, and the second one is only ever true of the accounts it could
    be measured over.

    **The concentration** is ``concentration`` — the same function the revenue
    and spend halves use, given rows whose ``money`` is what the customer still
    owes. Not a second implementation, so "the largest five" cannot mean one set
    of names on this figure and another set on the one beside it.

    **The weighted-average days-to-pay** is over exactly the customers
    ``concentration`` named, weighted by what each of them owes. Weighted rather
    than averaged because the question is how long *the money* has been out, and
    an account owing ₹40 lakh at 70 days and one owing ₹2 lakh at 20 days do not
    each contribute half of it.

    It is **not** days sales outstanding and must not be printed as one. DSO is
    a ratio against a revenue window, this is an average of measured settlement
    durations, and the two answer to different denominators.

    **Where the evidence runs out.** A customer's figure comes from
    ``payments.lag``, which refuses below its settlement floor — so an account
    that has settled once contributes nothing rather than contributing a number
    read out of one invoice. What is *not* done is quietly averaging the rest
    and calling the answer "the top five": ``covers_share`` says how much of the
    top five's money the figure actually spans and ``unmeasured`` names the
    accounts it does not, so the number can never be read as covering more than
    it does. With nothing measurable at all the figure is ``None`` — UNKNOWN,
    not a benign default.

    There is deliberately no minimum coverage below which the figure is
    suppressed. That would be a band edge, and a band edge is policy that has to
    carry a version (working agreement §1); stating the coverage beside the
    number is the same protection without inventing an unversioned threshold.
    """
    # Only customers who owe something. A zero balance is not a small share of
    # the book, it is not part of the book at all, and counting it would make
    # "the largest five of 340 customers" out of 40 who actually owe money.
    rows = [
        Standing(
            entity_id=o.customer_id,
            label=names.get(o.customer_id) or f"Unnamed customer (id {o.customer_id})",
            side=RECEIVABLE,
            money=float(o.outstanding),
            share=None,
        )
        for o in owings if o.outstanding > 0
    ]
    book = sum(r.money for r in rows)
    for r in rows:
        r.share = (r.money / book) if book else None
    rows.sort(key=lambda r: -r.money)

    conc = concentration(rows)
    by_id = {r.entity_id: r for r in rows}
    top = [by_id[i] for i in conc["top_n_ids"]]

    measured = [(r, lags[r.entity_id]) for r in top if r.entity_id in lags]
    weight = sum(r.money for r, _ in measured)
    unmeasured = [r for r in top if r.entity_id not in lags]
    top_money = sum(r.money for r in top)

    return {
        "rows": [{**r.to_dict(),
                  # The component figures, so a reader can see which accounts
                  # the weighted number is made of rather than taking it on
                  # trust. ``None`` is "below the settlement floor", which is
                  # the same claim ``unmeasured`` makes about the same row.
                  "days_to_pay": (lags[r.entity_id].expected_days_to_pay
                                  if r.entity_id in lags else None),
                  "settlements": (lags[r.entity_id].settlements
                                  if r.entity_id in lags else 0)}
                 for r in rows],
        "total": round(book, 2),
        "concentration": conc,
        "days_to_pay": {
            "weighted_days": (round(sum(r.money * lg.expected_days_to_pay
                                        for r, lg in measured) / weight, 1)
                              if weight else None),
            "measured": len(measured),
            "of": len(top),
            # Of the top five's money, how much the figure above spans. The
            # number is meaningless without this and travels with it.
            "covers_share": (round(weight / top_money, 4) if top_money else None),
            "unmeasured": [{"entity_id": r.entity_id, "label": r.label,
                            "outstanding": round(r.money, 2)}
                           for r in unmeasured],
            "min_settlements": MIN_SETTLEMENTS,
        },
    }


# ── the whole book as one picture ───────────────────────────────────────────
#
# Principals → lines → customers, with the width of every band the money
# running through it. The two dependency lists above answer "how exposed are we
# to this name"; this answers the question an owner actually asks first, which
# is "what does my business look like" — and it answers both ends and the mix in
# the middle at once, which no list can.
#
# **Nothing is silently dropped.** Revenue that could not be traced to a
# principal gets its own band, and so does trade in items no line could be
# resolved for. A flow picture that quietly omitted them would show a smaller,
# tidier business than the real one and would not reconcile with the totals on
# every other screen.
#
# **The tail is folded, and the fold is labelled with its count.** A band per
# customer is a hairball at two hundred; "Other (183)" is a band somebody can
# read, and the number in it says how much was folded.

#: The node stages, in the order they are drawn.
STAGE_VENDOR = 0
STAGE_LINE = 1
STAGE_CUSTOMER = 2

UNTRACED = "__untraced__"
UNPLACED = "__unplaced__"
OTHER_VENDORS = "__other_vendors__"
OTHER_CUSTOMERS = "__other_customers__"


def sankey(flows: Iterable[Flow], *, vendor_names: dict[str, str],
           customer_names: dict[str, str],
           top_vendors: int = 8, top_customers: int = 8) -> dict:
    """The book as bands of money, principal through line to customer."""
    rows = list(flows)
    if not rows:
        return {"nodes": [], "links": [], "total": 0.0,
                "folded": {"vendors": 0, "customers": 0}}

    def total_by(key) -> dict[str, float]:
        out: dict[str, float] = {}
        for f in rows:
            out[key(f)] = out.get(key(f), 0.0) + f.revenue
        return out

    vendor_total = total_by(lambda f: f.vendor_id or UNTRACED)
    customer_total = total_by(lambda f: f.customer_id)

    # Which names survive as their own band. The rest fold, and the fold says
    # how many went into it.
    keep_v = {v for v, _ in sorted(
        ((v, m) for v, m in vendor_total.items() if v != UNTRACED),
        key=lambda kv: -kv[1])[:top_vendors]}
    keep_c = {c for c, _ in sorted(customer_total.items(),
                                   key=lambda kv: -kv[1])[:top_customers]}

    def vendor_of(f: Flow) -> str:
        if not f.vendor_id:
            return UNTRACED
        return f.vendor_id if f.vendor_id in keep_v else OTHER_VENDORS

    def line_of(f: Flow) -> str:
        return f.category if (f.category and f.category != UNCATEGORISED) else UNPLACED

    def customer_of(f: Flow) -> str:
        return f.customer_id if f.customer_id in keep_c else OTHER_CUSTOMERS

    left: dict[tuple[str, str], float] = {}
    right: dict[tuple[str, str], float] = {}
    weight: dict[str, float] = {}
    for f in rows:
        v, ln, c = vendor_of(f), line_of(f), customer_of(f)
        left[(v, ln)] = left.get((v, ln), 0.0) + f.revenue
        right[(ln, c)] = right.get((ln, c), 0.0) + f.revenue
        for node in (v, ln, c):
            weight[node] = weight.get(node, 0.0) + f.revenue

    folded_v = len([v for v in vendor_total if v not in keep_v and v != UNTRACED])
    folded_c = len([c for c in customer_total if c not in keep_c])

    def label(node: str, stage: int) -> str:
        if node == UNTRACED:
            return "Not traced to a principal"
        if node == UNPLACED:
            return "Line not resolved"
        if node == OTHER_VENDORS:
            return f"Other suppliers ({folded_v})"
        if node == OTHER_CUSTOMERS:
            return f"Other customers ({folded_c})"
        if stage == STAGE_VENDOR:
            return vendor_names.get(node) or f"Unnamed supplier (id {node})"
        if stage == STAGE_CUSTOMER:
            return customer_names.get(node) or f"Unnamed customer (id {node})"
        return LABELS.get(node, node)

    stages = {STAGE_VENDOR: {v for v, _ in left},
              STAGE_LINE: {ln for _, ln in left} | {ln for ln, _ in right},
              STAGE_CUSTOMER: {c for _, c in right}}
    nodes = [
        {"id": f"{stage}:{node}", "key": node, "stage": stage,
         "label": label(node, stage), "money": round(weight.get(node, 0.0), 2),
         # Named so a screen can style the honest bands differently from the
         # real ones without re-deriving which is which.
         "residual": node in (UNTRACED, UNPLACED, OTHER_VENDORS, OTHER_CUSTOMERS)}
        for stage, names in sorted(stages.items())
        for node in sorted(names, key=lambda n: -weight.get(n, 0.0))
    ]
    links = (
        [{"source": f"{STAGE_VENDOR}:{v}", "target": f"{STAGE_LINE}:{ln}",
          "money": round(m, 2)} for (v, ln), m in left.items()]
        + [{"source": f"{STAGE_LINE}:{ln}", "target": f"{STAGE_CUSTOMER}:{c}",
            "money": round(m, 2)} for (ln, c), m in right.items()]
    )
    return {
        "nodes": nodes,
        "links": sorted(links, key=lambda kv: -kv["money"]),
        "total": round(sum(f.revenue for f in rows), 2),
        "folded": {"vendors": folded_v, "customers": folded_c},
    }


def unavailable() -> list[dict]:
    """The claims this view is not entitled to make."""
    return [
        {
            "what": "Days sales outstanding",
            # Not epistemic: the platform holds sales and it holds balances.
            # What it does not hold is the two of them reconciled into one
            # ratio, and pretending the weighted average beside it is that
            # ratio would be the cheapest way to get this wrong.
            "kind": absence.BUILDABLE,
            "why": ("The receivables fold is keyed by customer and carries no "
                    "revenue window, so the denominator DSO needs is not in it "
                    "— the reducer says so at length. What is shown instead is "
                    "the weighted-average days-to-pay of the largest five "
                    "accounts: an average of settlement durations actually "
                    "observed, not a ratio against sales. The two are close "
                    "enough in spirit to be confused and are not "
                    "interchangeable."),
        },
        {
            "what": "How much they depend on us",
            # Epistemic, not a gap: the platform will never see what a customer
            # buys elsewhere, however complete this book becomes.
            "kind": absence.PERMANENT,
            "why": ("This measures our exposure to a customer. The platform "
                    "sees what they buy here and nothing of what they buy "
                    "elsewhere, so a customer giving us a fifth of a large "
                    "spend and one giving us all of a small one look "
                    "identical. 'They are 8% of our revenue' is a fact; 'we "
                    "are 8% of their purchasing' would be a guess."),
        },
        {
            "what": "Whether a second source exists",
            "kind": absence.PERMANENT,
            "why": ("Sole-source counts say nobody else has supplied us an "
                    "item. That is not the same as nobody else being able to, "
                    "and the difference is a purchasing conversation rather "
                    "than a number this platform can produce."),
        },
    ]
