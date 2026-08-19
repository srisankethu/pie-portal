"""How much of a cost move reached the price — the pricing power a book reveals.

``signals/cost_pass_through.py`` already says *that* this is happening: a product
whose cost rose while its selling price did not follow. ``portfolio.py`` rolls
that up as ``items_cost_not_passed``, which is a count. A count separates "there
is a problem here" from "there is not", and it cannot separate the account that
takes 90% of every supplier increase without a phone call from the one that takes
15% of it after three. Those are different businesses to be in, and the count
scores them identically once both have one flagged item.

So this is the magnitude: **Δ realised price ÷ Δ cost**, over a window, per
customer × item and rolled up per customer × line of business. 1.0 is full
pass-through. 0.0 is a price-taker. Above 1.0 is a supplier increase used as
cover for a rise larger than the increase, which is a real and reportable thing
rather than an error.

**Realised price, not list price.** The numerator is what was actually charged
after discount, quantity-weighted, because a list price nobody paid reveals
nothing about a negotiation. That is ``metrics``' own weighting, and this module
does not have a second one — see below.

---

**This is not elasticity, and the difference is not pedantry.**
``insight/simulate.py`` draws this line for the price simulator and it is the
same line here. Prices in this book are negotiated per quote, not posted. So an
observed (price, quantity) pair was *selected by the negotiation*: we conceded
where we expected to lose the order and held where we did not. Fitting a demand
curve through those points recovers our own bargaining behaviour and returns it
wearing the authority of a measured elasticity — a number that would then be used
to set the next price, closing the loop on itself.

What is measurable without that leap is narrower and honest: given a cost move we
did not choose, how much of it reached the price. The cost move is exogenous to
the negotiation in a way the quantity never is, which is exactly why this ratio
is reportable and an elasticity is not. **Nothing in this module may grow a
quantity term in the denominator.**

**Volume is reported beside the ratio and never folded into it.** Full
pass-through with the volume collapsing is not pricing power; it is an account
walking away politely. A reader needs both facts and needs them separate.
``insight/weather.py`` records why the composite version is refused: a single
score compresses independent findings into a comparison nobody can invert, and
the weighting that produces it is an unstated opinion presented as arithmetic.
There is no ``pricing_power_score`` here and there should not be one.

---

**The degenerate cases are the whole difficulty, and every one of them is
refused rather than smoothed.** Each has an arithmetic answer that looks
plausible and is a lie:

- a **near-zero cost move** makes the denominator explode. ₹0.40 of drift under
  a ₹12 price rise is a pass-through of 30, and it would sort above every honest
  row on the page. Refused against ``pass_through_min_cost_move_pct``, not capped
  — a capped 5.0 still sorts to the top and still means nothing;
- an item with **one cost point** has no movement at all. Not zero pass-through:
  the supplier has not been observed to move, so the question has not been asked;
- a line with **no cost record** has neither, and answering 1.0 there would
  report perfect pricing power on the platform's own ignorance — the failure
  ``CLAUDE.md`` records three times over;
- at the roll-up, **offsetting cost moves** reproduce the exploding denominator
  one level up: one item's cost rising and another's falling net to almost
  nothing while both were individually material, and the group ratio goes to
  infinity for a reason that has nothing to do with how this customer buys. The
  group refuses and its items still show.

Both floors are ``CommercialThresholds`` fields, inside the version hash, for the
reason every classification in that file is: they decide which rows have a figure
at all, and a screen that silently gained forty rows between two quarters is one
nobody can reconcile.

**The arithmetic is not here.** ``metrics.pass_through`` computes the per-item
figure and owns every refusal above except the group one, because it already owns
the two windows and the quantity-weighted price and cost that
``classify_erosion`` reads. Two modules measuring "how far did cost and price
move" would drift on what "materially" means and then disagree with each other on
one screen. This module groups, weights and explains; it measures nothing.

**Weighted, never averaged.** A category's pass-through is Σ price move ÷ Σ cost
move *in money* — the same rule as Σ gross profit ÷ Σ revenue, and for the same
reason. The mean of per-item ratios lets a ₹900 item weigh as much as a ₹9 lakh
one, and on this ratio it is worse than usual: the small item is also the one
most likely to carry a near-degenerate denominator.

**Lines of business come from ``commercial/categories.py``**, with the provenance
it resolves. There is no second mapping here, and an item that module could not
place stays honestly uncategorised rather than being swept into a line to make a
grid look complete.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
**Manager and owner only, and structurally so.** A pass-through ratio is
computed against cost movement, which makes it a margin question by
construction: hand a salesperson the ratio and the price move and they have the
cost move. There is no version of this view with the economics removed, so the
endpoint refuses outright rather than serving a projection with the middle taken
out — the same call ``/gmroi`` and ``/quote-pricing`` already make.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from .. import categories as cat
from .. import metrics
from ..config import CommercialThresholds
from . import absence

_ZERO = Decimal("0")

#: The roll-up's own two ways of having no figure. The item-level codes live in
#: ``metrics`` beside the arithmetic that produces them; these two are properties
#: of a *set* of items and have nowhere else to live.
NO_MEASURED_ITEM = "NO_MEASURED_ITEM"
OFFSETTING_COST_MOVES = "OFFSETTING_COST_MOVES"

#: The key the whole-customer roll-up carries. Never a category key, so nothing
#: downstream can hand it to something expecting one.
ALL_LINES = "ALL_LINES"
ALL_LINES_LABEL = "Every line"

REASON_MEANING: dict[str, str] = {
    metrics.NO_COST_ON_RECORD: (
        "No bill covers this item in the window, so there is no cost movement "
        "to divide by. Not full pass-through and not none of it — the question "
        "has no denominator."),
    metrics.SINGLE_COST_POINT: (
        "The item has been bought at one price only, so the supplier has not "
        "been observed to move. There is nothing for a selling price to have "
        "followed, which is different from a price that failed to follow."),
    metrics.COST_MOVE_IMMATERIAL: (
        "The cost moved by less than the materiality floor. Dividing by it "
        "would report rounding in a supplier's invoice as pricing power, so no "
        "ratio is produced rather than a very large one."),
    metrics.NO_PRICE_IN_WINDOW: (
        "Nothing was charged for this item in the recent window, so no realised "
        "price moved. A dormant relationship, not a pass-through of nil."),
    NO_MEASURED_ITEM: (
        "No item in this group had a measurable cost move behind it, so there "
        "is nothing to weight. The item rows say which of the four reasons "
        "applies to each."),
    OFFSETTING_COST_MOVES: (
        "Costs moved materially in both directions and very nearly cancelled, "
        "so the group's net cost move is too small to divide by even though "
        "each item's was not. A ratio here would be an artefact of the "
        "cancellation. Read the items instead."),
}

#: What *kind* of refusal each one is, in ``absence``'s taxonomy, so a reader can
#: tell a worklist item from a limit. Three of the six resolve themselves as the
#: book keeps trading, one is somebody's data-entry job, and two are properties
#: of the arithmetic that no amount of data changes.
REASON_KIND: dict[str, str] = {
    # A bill exists in the world and has not been entered or not been synced.
    metrics.NO_COST_ON_RECORD: absence.COLLECTABLE,
    # Buy the item a second time at a different price and the figure appears.
    metrics.SINGLE_COST_POINT: absence.TRANSIENT,
    # The supplier held their price. Nobody should act on this; it answers
    # itself the next time they do not.
    metrics.COST_MOVE_IMMATERIAL: absence.TRANSIENT,
    metrics.NO_PRICE_IN_WINDOW: absence.TRANSIENT,
    NO_MEASURED_ITEM: absence.TRANSIENT,
    # No further data resolves this one: the group's moves really did cancel.
    OFFSETTING_COST_MOVES: absence.PERMANENT,
}


def _f(value: Optional[Decimal]) -> Optional[float]:
    return round(float(value), 2) if value is not None else None


def _ratio(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


@dataclass(frozen=True)
class Item:
    """One customer × item relationship, as this view needs it.

    Rows rather than a session, the shape ``gmroi`` and ``mix`` already take, so
    the grouping is a function of its inputs and the router keeps the database.
    """

    customer_id: str
    product_id: str
    label: str
    #: From ``commercial/categories``. ``UNCATEGORISED`` is a real value and is
    #: grouped as its own line rather than dropped.
    category: str
    category_source: str
    pass_through: metrics.PassThrough
    #: Volume, carried whole and never multiplied into the ratio.
    qty_recent: Decimal
    qty_previous: Decimal
    volume_change_pct: Optional[float]
    revenue_recent: Decimal
    revenue_previous: Decimal

    def to_dict(self) -> dict:
        pt = self.pass_through
        return {
            "product_id": self.product_id,
            "label": self.label,
            "category": self.category,
            "category_label": cat.LABELS.get(self.category, self.category),
            "category_source": self.category_source,
            "pass_through": _ratio(pt.ratio),
            "cost_move_per_unit": _f(pt.cost_move_per_unit),
            "price_move_per_unit": _f(pt.price_move_per_unit),
            "cost_move_value": _f(pt.cost_move_value),
            "price_move_value": _f(pt.price_move_value),
            "cost_points": pt.cost_points,
            "reason": pt.reason,
            "reason_meaning": REASON_MEANING.get(pt.reason) if pt.reason else None,
            "reason_kind": REASON_KIND.get(pt.reason) if pt.reason else None,
            # Beside the ratio, never inside it.
            "volume": {
                "qty_recent": _f(self.qty_recent),
                "qty_previous": _f(self.qty_previous),
                "change_pct": _ratio(self.volume_change_pct),
                "revenue_recent": _f(self.revenue_recent),
                "revenue_previous": _f(self.revenue_previous),
            },
        }


def from_metrics(m: metrics.RelationshipMetrics, *, label: str,
                 resolution: Optional[cat.Resolution]) -> Item:
    """One computed relationship, as a row of this view.

    Here rather than in the router so the mapping from ``RelationshipMetrics`` to
    this view exists once. A router that picked the fields itself would be the
    place a later field silently stopped being carried.
    """
    return Item(
        customer_id=m.customer_id,
        product_id=m.product_id,
        label=label,
        category=(resolution.category if resolution is not None
                  else cat.UNCATEGORISED),
        category_source=(resolution.source if resolution is not None
                         else cat.BY_NOTHING),
        pass_through=m.pass_through,
        qty_recent=m.qty_recent,
        qty_previous=m.qty_previous,
        volume_change_pct=m.volume_change_pct,
        revenue_recent=m.revenue_recent,
        revenue_previous=(m.previous.revenue if m.previous is not None else _ZERO),
    )


@dataclass
class Group:
    """A customer × line-of-business roll-up. Σ price move ÷ Σ cost move.

    Never the mean of the items' ratios. Two reasons rather than the usual one:
    the mean lets a ₹900 item weigh as much as a ₹9 lakh one, *and* the small
    item is systematically the one carrying the near-degenerate denominator, so
    an average is not merely unweighted but biased toward the rows that mean
    least.
    """

    key: str
    label: str
    #: Signed. Rises and falls cancel here on purpose — that is what a net move
    #: is — and ``_finalise`` refuses the ratio when they cancel too well.
    cost_move_value: Decimal = _ZERO
    price_move_value: Decimal = _ZERO
    #: The same moves without their signs, so the cancellation is visible rather
    #: than inferred from a ratio that looks odd.
    gross_cost_move_value: Decimal = _ZERO
    items: int = 0
    items_measured: int = 0
    #: Why the unmeasured items are unmeasured, counted. A group where every
    #: absence is one reason is a different problem from one where they are four.
    reasons: dict[str, int] = field(default_factory=dict)
    #: Volume, over exactly the items the ratio speaks for. Counted rather than
    #: summed: quantities across a coolant drum and a carbide insert are not
    #: addable, and a total that pretends otherwise is worse than three counts.
    volume_up: int = 0
    volume_down: int = 0
    volume_flat: int = 0
    volume_unknown: int = 0
    revenue_recent: Decimal = _ZERO
    revenue_previous: Decimal = _ZERO
    reason: Optional[str] = None

    @property
    def ratio(self) -> Optional[float]:
        """Weighted pass-through, or ``None`` where the group refused.

        The guard is on the arithmetic: a refused group returns nothing here
        rather than falling through to a division whose denominator was the
        thing objected to.
        """
        if self.reason is not None:
            return None
        if self.cost_move_value == _ZERO:
            return None
        return float(self.price_move_value / self.cost_move_value)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "pass_through": _ratio(self.ratio),
            "cost_move_value": _f(self.cost_move_value),
            "price_move_value": _f(self.price_move_value),
            "gross_cost_move_value": _f(self.gross_cost_move_value),
            "items": self.items,
            "items_measured": self.items_measured,
            "reasons": dict(sorted(self.reasons.items())),
            "reason": self.reason,
            "reason_meaning": (REASON_MEANING.get(self.reason)
                               if self.reason else None),
            "reason_kind": REASON_KIND.get(self.reason) if self.reason else None,
            "volume": {
                "items_up": self.volume_up,
                "items_down": self.volume_down,
                "items_flat": self.volume_flat,
                "items_unknown": self.volume_unknown,
                "revenue_recent": _f(self.revenue_recent),
                "revenue_previous": _f(self.revenue_previous),
                "basis": ("Counted over the items the ratio is computed from. "
                          "Quantities are not summed across items — a drum and "
                          "an insert do not add — so the movement is reported "
                          "as how many items rose, fell or held."),
            },
        }


def _accumulate(group: Group, item: Item, th: CommercialThresholds) -> None:
    """Fold one item into a group. Money in ``Decimal``, volume as counts."""
    group.items += 1
    pt = item.pass_through
    if not pt.measured:
        group.reasons[pt.reason] = group.reasons.get(pt.reason, 0) + 1
        return

    # ``measured`` is exactly the guard both value properties apply, so neither
    # is None here — but the sums are written so that a future third state
    # cannot silently contribute a zero.
    cost_move = pt.cost_move_value
    price_move = pt.price_move_value
    if cost_move is None or price_move is None:      # pragma: no cover - defensive
        group.reasons[metrics.NO_COST_ON_RECORD] = \
            group.reasons.get(metrics.NO_COST_ON_RECORD, 0) + 1
        return

    group.items_measured += 1
    group.cost_move_value += cost_move
    group.gross_cost_move_value += abs(cost_move)
    group.price_move_value += price_move
    group.revenue_recent += item.revenue_recent
    group.revenue_previous += item.revenue_previous

    change = item.volume_change_pct
    if change is None:
        group.volume_unknown += 1
    elif change >= th.meaningful_volume_change_pct:
        group.volume_up += 1
    elif change <= -th.meaningful_volume_change_pct:
        group.volume_down += 1
    else:
        group.volume_flat += 1


def _finalise(group: Group, th: CommercialThresholds) -> Group:
    """Decide whether the group has a ratio at all, before anybody reads one."""
    if group.items_measured == 0:
        group.reason = NO_MEASURED_ITEM
        return group
    net = abs(group.cost_move_value)
    gross = group.gross_cost_move_value
    if gross <= _ZERO or (net / gross) < Decimal(
            str(th.pass_through_min_net_cost_move_share)):
        group.reason = OFFSETTING_COST_MOVES
    return group


def _group_items(items: list[Item], th: CommercialThresholds) -> list[Group]:
    """One group per line of business this customer traded, in the reading order.

    ``categories.ORDER`` decides the order and ``UNCATEGORISED`` goes last —
    every screen built on that module renders the lines as columns, and an order
    that varies per view is a grid nobody can scan twice.
    """
    groups: dict[str, Group] = {}
    for item in items:
        group = groups.get(item.category)
        if group is None:
            group = Group(key=item.category,
                          label=cat.LABELS.get(item.category, item.category))
            groups[item.category] = group
        _accumulate(group, item, th)

    order = {key: i for i, key in enumerate(cat.ORDER)}
    return [_finalise(g, th) for _, g in sorted(
        groups.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))]


def _harm(price_move_value, cost_move_value) -> float:
    """Margin given away, as a sortable float. Most negative first.

    ``price_move − cost_move`` in money: below zero the cost move outran the
    price move and the relationship is worse off, above zero it is better off.
    Deliberately not the ratio — see ``build`` for why the ratio cannot order
    this — and deliberately money rather than per-unit, so a large line outranks
    a small one at the same per-unit slip.
    """
    if price_move_value is None or cost_move_value is None:
        return 0.0
    return float(price_move_value - cost_move_value)


def _customer_total(items: list[Item], th: CommercialThresholds) -> Group:
    """The customer's own figure, across every line.

    Computed from the items again rather than by summing the groups. Summing
    groups would be the same arithmetic only while every group is included, and
    a refused group must not silently drop its items out of the customer's
    total — the offsetting-moves refusal is about a *ratio*, not about the money
    that produced it.
    """
    total = Group(key=ALL_LINES, label=ALL_LINES_LABEL)
    for item in items:
        _accumulate(total, item, th)
    return _finalise(total, th)


def build(items: Iterable[Item], *, customer_names: dict[str, str],
          as_of: date, thresholds: CommercialThresholds) -> dict:
    """Every customer's revealed pass-through, by item and by line of business.

    Ordered worst first — the accounts absorbing the least of their cost moves
    are the ones a pricing conversation is owed, and a page sorted by name buries
    them. Customers with no measurable item sort last rather than as zero: "we
    cannot say" is not "they passed nothing on", and putting an unknown at the
    top of a worst-first list is exactly the mistake this module refuses to make
    at the row level.
    """
    rows = list(items)
    by_customer: dict[str, list[Item]] = {}
    for item in rows:
        by_customer.setdefault(item.customer_id, []).append(item)

    # **Ranked on what the moves did to margin, not on the ratio.** The ratio
    # alone is blind to the sign of the cost move, and on a falling cost it
    # inverts: holding price while cost drops is the best outcome on the page
    # and scores ~0, while passing the whole saving on scores ~1. Sorting
    # ascending by ratio therefore puts the account that defended its margin at
    # the top of a worklist headed "worst first". Price move less cost move, in
    # money, is signed correctly in both directions — negative is margin given
    # away — and it is already computed on both the group and the item.
    ranked: list[tuple[tuple[bool, float, str], dict]] = []
    for customer_id, own in by_customer.items():
        own.sort(key=lambda i: (i.pass_through.ratio is None,
                                _harm(i.pass_through.price_move_value,
                                      i.pass_through.cost_move_value),
                                i.product_id))
        overall = _customer_total(own, thresholds)
        ranked.append((
            # A customer with no figure sorts last, not as a zero.
            (overall.ratio is None,
             _harm(overall.price_move_value, overall.cost_move_value),
             customer_id),
            {
                "customer_id": customer_id,
                "label": (customer_names.get(customer_id)
                          or f"Unnamed customer (id {customer_id})"),
                "overall": overall.to_dict(),
                "categories": [g.to_dict()
                               for g in _group_items(own, thresholds)],
                "items": [i.to_dict() for i in own],
            }))

    customers = [row for _, row in sorted(ranked, key=lambda pair: pair[0])]
    measured = [c for c in customers if c["overall"]["pass_through"] is not None]
    return {
        "as_of": as_of.isoformat(),
        "customers": customers,
        "counts": {
            "customers": len(customers),
            "customers_measured": len(measured),
            "items": len(rows),
            "items_measured": sum(1 for i in rows if i.pass_through.measured),
        },
        "floors": {
            "min_cost_move_pct": thresholds.pass_through_min_cost_move_pct,
            "min_net_cost_move_share":
                thresholds.pass_through_min_net_cost_move_share,
        },
        "reason_meanings": REASON_MEANING,
        "unavailable": unavailable(),
        "thresholds_version": thresholds.version,
    }


def unavailable() -> list[dict]:
    """What this view will not say, and why — stated where it will be read.

    All three are refusals of a *number somebody will ask for*, which is the only
    kind worth listing. A gap with no explanation reads as a bug and gets
    "fixed"; an argued refusal is the thing that keeps getting made.
    """
    return [
        {
            "series": "price_elasticity_of_demand",
            # PERMANENT, and not for want of data. More observations make the
            # estimate tighter around the wrong quantity.
            "kind": absence.PERMANENT,
            "reason": ("Prices here are negotiated per quote rather than "
                       "posted, so an observed price and quantity were chosen "
                       "together by the negotiation: we conceded where we "
                       "expected to lose the order and held where we did not. "
                       "A curve fitted through those points measures our own "
                       "bargaining, not demand, and would then be used to set "
                       "the next price. What is measurable is how much of a "
                       "cost move we did not choose reached the price, which is "
                       "the ratio on every row."),
        },
        {
            "series": "combined_pricing_power_score",
            "kind": absence.PERMANENT,
            "reason": ("Pass-through and volume are reported side by side and "
                       "are deliberately not composed. Full pass-through with "
                       "the volume collapsing is an account leaving politely, "
                       "and a single score would rank it beside an account that "
                       "held both — with a weighting nobody voted on doing the "
                       "ranking. Read the two together; they are on the same "
                       "row."),
        },
        {
            "series": "pass_through_still_in_flight",
            # TRANSIENT: the next window answers it, and putting "wait a
            # quarter" on somebody's worklist is noise.
            "kind": absence.TRANSIENT,
            "reason": ("A cost increase that landed late in the recent window "
                       "has had less time to reach a price than one that landed "
                       "early, so a repricing already agreed for next quarter "
                       "reads here as under-pass-through. The ratio is what has "
                       "happened, not what is agreed."),
        },
    ]
