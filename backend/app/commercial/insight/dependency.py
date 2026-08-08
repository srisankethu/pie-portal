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

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
The vendor half is denominated in purchase spend, so the router — not this
module — decides who may read it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from ..categories import LABELS, UNCATEGORISED
from ..config import CommercialThresholds

#: Which end of the book a row describes.
VENDOR = "vendor"
CUSTOMER = "customer"

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
class Target:
    """What a principal expects, over an explicit period."""

    vendor_id: str
    period_start: date
    period_end: date
    basis: str
    amount: float

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
            "concentration": _concentration(customers),
        },
        "vendors": (None if vendors is None else {
            "rows": [s.to_dict() for s in vendors],
            "total": round(sum(s.money for s in vendors), 2),
            "concentration": _concentration(vendors),
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


def _concentration(rows: list[Standing]) -> dict:
    """How much of this side sits with the largest few.

    Shares of a *total*, so they are only meaningful over the whole book — which
    is why the screen's company filter narrows the rows it lists and never
    restates these. ``CompanyFilter`` documents the same rule.
    """
    total = sum(r.money for r in rows)
    if not rows or not total:
        return {"top_share": None, "top_label": None, "top_n_share": None,
                "count": len(rows)}
    ranked = sorted(rows, key=lambda r: -r.money)
    return {
        "top_share": round(ranked[0].money / total, 4),
        "top_label": ranked[0].label,
        "top_n_share": round(sum(r.money for r in ranked[:TOP_N]) / total, 4),
        "count": len(rows),
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
    """The two claims this view is not entitled to make."""
    return [
        {
            "what": "How much they depend on us",
            "why": ("This measures our exposure to a customer. The platform "
                    "sees what they buy here and nothing of what they buy "
                    "elsewhere, so a customer giving us a fifth of a large "
                    "spend and one giving us all of a small one look "
                    "identical. 'They are 8% of our revenue' is a fact; 'we "
                    "are 8% of their purchasing' would be a guess."),
        },
        {
            "what": "Whether a second source exists",
            "why": ("Sole-source counts say nobody else has supplied us an "
                    "item. That is not the same as nobody else being able to, "
                    "and the difference is a purchasing conversation rather "
                    "than a number this platform can produce."),
        },
    ]
