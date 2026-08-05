"""What is on the shelf, and which of it is a problem.

Four questions, and the platform can now answer three of them honestly:

**Committed beyond what exists.** Zoho reports ``available`` (what can still be
sold) and ``actual_available`` (that, net of what open orders have already
promised). A negative actual against a positive available means more has been
committed than is on the shelf — a delivery date somebody is going to miss.
This is the one finding on this screen that is *already* a decision.

**Sitting still.** Stock on hand that nothing has sold in the window. Not
"dead" — that is a judgement about a future nobody can see — but measured, with
the date of the last sale attached so a person can make the judgement.

**No reorder point.** Zoho's ``reorder_level`` is blank on most of this book's
items. That is a real finding about the master data, and it is reported as one.
It is *not* substituted with a computed reorder point: a reorder level is a
policy somebody chooses with lead time and service level in mind, and inventing
one here would be the platform making a commercial decision in a module whose
whole purpose is not to.

**Cover in weeks.** Deliberately absent. It needs a demand forecast, and the
honest forecast from this data is "what sold in the last N days", which turns
into a weeks-of-cover number that reads like a projection and is not one. Named
in the response so the screen can say what it does not know.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

#: On hand, and nothing sold in this many days. Long, deliberately: a
#: distributor's slow-moving tooling line is not dead in month two. The
#: organization's own dead/slow thresholds override this — see ``Carrying``.
IDLE_AFTER_DAYS = 180

#: Health bands, worst last. The screen colours on these and nothing else, so
#: "amber" means one thing across every surface rather than one thing per chart.
HEALTHY, SLOW, DEAD = "HEALTHY", "SLOW", "DEAD"

#: What to do about a line. Deterministic — a band, not a recommendation
#: somebody wrote. Ordered by how much of a decision each one is.
ACTIONS: dict[str, str] = {
    "HOLD": "Nothing to do",
    "PUSH": "Push it — offer it before discounting it",
    "DISCOUNT": "Discount to move",
    "BUNDLE": "Bundle with what does sell",
    "RETURN": "Ask the supplier to take it back",
    "WRITE_OFF": "Propose a write-off",
}


@dataclass(frozen=True)
class Carrying:
    """The carrying-cost policy, in one object so it cannot be half-applied.

    Built from ``CommercialThresholds`` at the router; kept as its own type so
    the arithmetic below has one thing to read and one thing to test against.
    """

    annual_pct: float
    dead_days: int
    slow_days: int

    @property
    def monthly_pct(self) -> float:
        return self.annual_pct / 12.0


@dataclass
class StockLine:
    product_id: str
    label: str
    on_hand: float
    available: Optional[float]
    actual_available: Optional[float]
    reorder_level: Optional[float]
    last_sold: Optional[date]
    sold_qty_window: float
    #: Cost. Present only for the roles allowed to see economics — the router
    #: drops the field entirely for a salesperson rather than zeroing it.
    purchase_rate: Optional[float] = None
    #: When we last bought it. Owner zone: paired with the purchase rate it is
    #: a supplier's price on a date.
    last_purchased: Optional[date] = None
    #: Customers who have bought this item before, most recent first. Their
    #: names only — operational, and the single most useful thing on a dead
    #: stock row, because "who do I call about this" is the actual question.
    buyers: tuple[str, ...] = ()

    # ── the money on the shelf ───────────────────────────────────────────────
    #
    # Everything below is a function of ``purchase_rate``, which means every one
    # of them is RESTRICTED. ``to_dict`` decides what leaves; these compute.

    def inventory_value(self) -> Optional[float]:
        if self.purchase_rate is None or self.on_hand <= 0:
            return None
        return round(self.on_hand * self.purchase_rate, 2)

    def monthly_holding_cost(self, c: Carrying) -> Optional[float]:
        """Value x annual rate / 12. What this line costs to keep for a month."""
        value = self.inventory_value()
        return None if value is None else round(value * c.monthly_pct, 2)

    def annual_holding_cost(self, c: Carrying) -> Optional[float]:
        value = self.inventory_value()
        return None if value is None else round(value * c.annual_pct, 2)

    def health(self, as_of: date, c: Carrying) -> str:
        """Healthy, slow or dead. Age alone — value does not make stock fresh."""
        if self.on_hand <= 0:
            return HEALTHY
        days = self.idle_days(as_of)
        if days is None or days >= c.dead_days:
            # Never sold at all, and on the shelf, is the strongest version of
            # dead — not a reason to exclude the row.
            return DEAD
        return SLOW if days >= c.slow_days else HEALTHY

    def priority(self, as_of: date, c: Carrying) -> Optional[float]:
        """What this line costs the business each month it is not sold.

        **Deliberately not a composite score.** A weighted blend of age,
        quantity and value produces a number between 0 and 100 that nobody can
        check and that changes meaning whenever a weight moves. The monthly
        holding cost is already the right ranking: it is in rupees, it is
        arithmetic anyone can redo, and a line that costs more to hold *is* the
        one to clear first. Age enters through the health band, which is shown
        beside it rather than blended into it.

        ``None`` where the cost is unknown, so an unpriced item sorts as
        unknown rather than as zero — bottom of a "worst first" list is exactly
        where a line nobody has costed should not silently sit.
        """
        return self.monthly_holding_cost(c)

    def recommended_action(self, as_of: date, c: Carrying) -> str:
        """What to do, from the age and whether it moves at all. A band.

        No model, no judgement about the future. The ladder is: healthy stock
        needs nothing; slow stock is worth *offering* before it is worth
        discounting; dead stock is discounted, then bundled, then returned, and
        only a line that has never sold at all is put up for write-off — which
        is a proposal to a person, never an instruction.
        """
        if self.on_hand <= 0:
            return "HOLD"
        band = self.health(as_of, c)
        if band == HEALTHY:
            return "HOLD"
        if band == SLOW:
            return "PUSH"
        days = self.idle_days(as_of)
        if days is None:
            # On the shelf and never sold once. Nothing about a discount is
            # evidenced here; it is a question for whoever bought it.
            return "WRITE_OFF"
        if days >= c.dead_days * 2:
            return "RETURN"
        return "DISCOUNT" if self.buyers else "BUNDLE"

    @property
    def oversold(self) -> bool:
        """Committed past what exists. Zoho's own arithmetic, not ours."""
        return self.actual_available is not None and self.actual_available < 0

    @property
    def below_reorder(self) -> bool:
        # A blank reorder level is not zero. An item with no reorder point set
        # can never be "below" it, and saying otherwise would flag the whole
        # catalogue the day somebody leaves the field empty.
        if self.reorder_level is None:
            return False
        return self.on_hand <= float(self.reorder_level)

    def idle_days(self, as_of: date) -> Optional[int]:
        return (as_of - self.last_sold).days if self.last_sold else None

    def idle(self, as_of: date) -> bool:
        if self.on_hand <= 0:
            return False
        days = self.idle_days(as_of)
        # Never sold at all, but sitting on the shelf, counts as idle: the
        # absence of a sale date is the strongest version of the finding, not
        # a reason to exclude the row.
        return days is None or days >= IDLE_AFTER_DAYS

    def to_dict(self, as_of: date, c: "Carrying", *, with_cost: bool) -> dict:
        """One row, projected for the reader.

        ────────────────────────────────────────────────────────────────────
        THE ONE THING TO BE CAREFUL ABOUT ON THIS SCREEN
        ────────────────────────────────────────────────────────────────────

        A salesperson gets ``monthly_holding_cost`` and does **not** get
        ``inventory_value``, ``purchase_rate`` or the carrying rate. That is a
        deliberate, and slightly uncomfortable, decision:

            monthly = quantity x cost x rate / 12

        so anyone holding the drain, the quantity *and the rate* recovers the
        purchase cost exactly. The quantity is on this row and has to be. The
        protection is therefore the rate alone — and unlike the floor markup,
        which varies by family and takes many observations to unpick, the
        carrying rate is one organization-wide constant. Disclosed once, every
        cost in the catalogue is computable, permanently.

        It is kept out of every operations payload and out of the Settings
        screen for any role below owner. **If that rate is ever published — in
        a policy document, a training deck, an email — this column has to come
        off the salesperson's screen the same day.** It is flagged here rather
        than solved because the alternative is not showing a salesperson what
        their dead stock costs, and that number is the entire point of the
        screen.
        """
        band = self.health(as_of, c)
        out = {
            "product_id": self.product_id, "label": self.label,
            "on_hand": self.on_hand, "available": self.available,
            "actual_available": self.actual_available,
            "reorder_level": self.reorder_level,
            "last_sold": self.last_sold.isoformat() if self.last_sold else None,
            "idle_days": self.idle_days(as_of),
            "sold_qty_window": round(self.sold_qty_window, 2),
            "oversold": self.oversold,
            "below_reorder": self.below_reorder,
            "idle": self.idle(as_of),
            # Age, health and what it costs to keep — operational, and the
            # three things a decision about clearing stock is made from.
            "health": band,
            "monthly_holding_cost": self.monthly_holding_cost(c),
            "priority": self.priority(as_of, c),
            "action": self.recommended_action(as_of, c),
            "action_label": ACTIONS[self.recommended_action(as_of, c)],
            # Who has bought it before. The answer to "who do I call about
            # this", which is what turns a dead-stock row into a phone call.
            "buyers": list(self.buyers[:6]),
        }
        if with_cost:
            out["purchase_rate"] = self.purchase_rate
            out["inventory_value"] = self.inventory_value()
            out["annual_holding_cost"] = self.annual_holding_cost(c)
            out["last_purchased"] = (self.last_purchased.isoformat()
                                     if self.last_purchased else None)
            # Kept under its old name as well: the earlier Stock screen and the
            # storyboard both read `stock_value`, and renaming a field to tidy
            # it up is how two screens start disagreeing about one number.
            out["stock_value"] = out["inventory_value"]
        return out


def build(lines: Iterable[StockLine], as_of: date, c: Carrying, *,
          with_cost: bool) -> dict:
    """The shelf, grouped by the jobs it implies."""
    rows = [r for r in lines]
    if not rows:
        return {"as_of": as_of.isoformat(), "groups": [], "counts": {},
                "items": [], "kpis": [], "filters": FILTERS,
                "unavailable": _unavailable()}

    oversold = [r for r in rows if r.oversold]
    below = [r for r in rows if r.below_reorder and not r.oversold]
    idle = [r for r in rows if r.idle(as_of) and not r.oversold]
    no_policy = [r for r in rows if r.reorder_level is None]

    # Worst first inside each group, by the thing that makes it worst.
    oversold.sort(key=lambda r: r.actual_available or 0)
    below.sort(key=lambda r: r.on_hand)
    idle.sort(key=lambda r: (r.idle_days(as_of) is None, r.idle_days(as_of) or 0),
              reverse=True)

    groups = [
        {
            "key": "OVERSOLD",
            "label": "Committed beyond stock",
            "meaning": ("Open orders promise more than is on the shelf. Zoho's "
                        "own netting, not an estimate — somebody is going to "
                        "miss a delivery date unless this is bought or "
                        "re-promised."),
            "items": [r.to_dict(as_of, c, with_cost=with_cost) for r in oversold[:40]],
            "count": len(oversold),
        },
        {
            "key": "BELOW_REORDER",
            "label": "At or below the reorder point",
            "meaning": ("Only for items where a reorder point has actually been "
                        "set. Items without one are counted separately, never "
                        "assumed to reorder at zero."),
            "items": [r.to_dict(as_of, c, with_cost=with_cost) for r in below[:40]],
            "count": len(below),
        },
        {
            "key": "IDLE",
            "label": f"On the shelf, nothing sold in {IDLE_AFTER_DAYS} days",
            "meaning": ("Measured, not judged. The last sale date is attached so "
                        "the call about whether it is dead stock stays with a "
                        "person who knows the line."),
            "items": [r.to_dict(as_of, c, with_cost=with_cost) for r in idle[:40]],
            "count": len(idle),
        },
    ]

    counts = {
        "tracked_items": len(rows),
        "oversold": len(oversold),
        "below_reorder": len(below),
        "idle": len(idle),
        "no_reorder_point": len(no_policy),
        "on_hand_items": sum(1 for r in rows if r.on_hand > 0),
    }
    if with_cost:
        counts["stock_value"] = round(
            sum(r.on_hand * r.purchase_rate for r in rows
                if r.purchase_rate is not None and r.on_hand > 0), 2)
        counts["value_unpriced_items"] = sum(
            1 for r in rows if r.on_hand > 0 and r.purchase_rate is None)

    # The grid: every line that is actually on the shelf, ranked by what it
    # costs to keep. Unpriced lines sort last rather than as zero — a line
    # nobody has costed is unknown, not free.
    on_shelf = [r for r in rows if r.on_hand > 0]
    on_shelf.sort(key=lambda r: (r.monthly_holding_cost(c) is None,
                                 -(r.monthly_holding_cost(c) or 0.0)))
    items = [r.to_dict(as_of, c, with_cost=with_cost) for r in on_shelf]

    return {
        "as_of": as_of.isoformat(),
        "groups": groups,
        "counts": counts,
        "items": items,
        "kpis": _kpis(on_shelf, as_of, c, with_cost=with_cost),
        "filters": FILTERS,
        "idle_after_days": IDLE_AFTER_DAYS,
        "dead_after_days": c.dead_days,
        "slow_after_days": c.slow_days,
        "unavailable": _unavailable(len(no_policy), len(rows)),
    }


#: The quick filters, defined here rather than in the client so the labels and
#: the predicates cannot drift. ``field``/``op``/``value`` is enough for the
#: grid to apply them without a round trip, and combinable because they are
#: independent predicates over one row.
FILTERS: list[dict] = [
    {"key": "DEAD", "label": "Dead", "field": "health", "op": "eq", "value": DEAD},
    {"key": "SLOW", "label": "Slow moving", "field": "health", "op": "eq",
     "value": SLOW},
    {"key": "NO_SALE_180", "label": "No sale in 180 days", "field": "idle_days",
     "op": "gte_or_null", "value": 180},
    {"key": "NO_SALE_365", "label": "No sale in 365 days", "field": "idle_days",
     "op": "gte_or_null", "value": 365},
    {"key": "NEVER_SOLD", "label": "Never sold", "field": "last_sold",
     "op": "is_null", "value": None},
    {"key": "OVERSOLD", "label": "Committed beyond stock", "field": "oversold",
     "op": "is_true", "value": None},
    # Cutoffs are the top decile of this book rather than a fixed rupee amount:
    # "high value" on a ₹19 crore book and a ₹37 lakh one are not the same
    # number, and a hardcoded one is wrong on at least one of them.
    {"key": "HIGH_DRAIN", "label": "Costliest to hold",
     "field": "monthly_holding_cost", "op": "top_decile", "value": None},
    {"key": "HIGH_QTY", "label": "Most on the shelf", "field": "on_hand",
     "op": "top_decile", "value": None},
]


def _kpis(rows: list[StockLine], as_of: date, c: Carrying, *,
          with_cost: bool) -> list[dict]:
    """The summary cards, each one clickable through to the rows behind it.

    Every card carries the filter that produced it, so pressing one narrows the
    grid to exactly the rows the number was computed from. A headline figure a
    person cannot drill into is a figure they have to take on trust.
    """
    dead = [r for r in rows if r.health(as_of, c) == DEAD]
    drains = [r.monthly_holding_cost(c) for r in rows]
    monthly = sum(d for d in drains if d is not None)
    values = [r.inventory_value() for r in rows]
    total_value = sum(v for v in values if v is not None)
    dead_value = sum(v for v in (r.inventory_value() for r in dead)
                     if v is not None)
    dead_monthly = sum(d for d in (r.monthly_holding_cost(c) for r in dead)
                       if d is not None)

    # Ordered as somebody reads them: what it costs, then how much of that is
    # stock nothing is selling, then how much cash that is. The cost-zone cards
    # are interleaved rather than appended, because "cash in quiet stock"
    # belongs beside "lines gone quiet" and not in a block of its own at the
    # end where it reads as a separate subject.
    cards: list[dict] = [
        {"key": "MONTHLY_DRAIN", "label": "Costing us every month",
         "value": round(monthly, 2), "unit": "money", "filter": None,
         "note": "What the shelf costs to keep, this month."},
    ]
    if with_cost:
        cards.append(
            {"key": "ANNUAL_DRAIN", "label": "Over a year",
             "value": round(monthly * 12, 2), "unit": "money", "filter": None,
             "note": "The monthly figure at the current carrying rate."})
    cards.append(
        {"key": "DEAD_DRAIN", "label": f"From stock idle {c.dead_days}+ days",
         "value": round(dead_monthly, 2), "unit": "money", "filter": "DEAD",
         "note": "The part of that drain nothing is currently selling."})
    cards.append(
        {"key": "DEAD_COUNT", "label": "Lines gone quiet",
         "value": len(dead), "unit": "count", "filter": "DEAD",
         "note": f"On the shelf with no sale in {c.dead_days} days."})
    if with_cost:
        share = (dead_value / total_value) if total_value else None
        cards.extend([
            {"key": "DEAD_VALUE", "label": "Cash in quiet stock",
             "value": round(dead_value, 2), "unit": "money", "filter": "DEAD",
             "note": ("What releasing it would return — before whatever "
                      "discount it takes to move.")},
            {"key": "DEAD_SHARE", "label": "Share of the shelf that is quiet",
             "value": (round(share, 4) if share is not None else None),
             "unit": "ratio", "filter": "DEAD",
             "note": "By value, not by line count."},
            {"key": "TOTAL_VALUE", "label": "Cash on the shelf",
             "value": round(total_value, 2), "unit": "money", "filter": None,
             "note": "At what it cost us, not at what it would sell for."},
        ])
    return cards


def _unavailable(no_policy: int = 0, total: int = 0) -> list[dict]:
    out = [{
        "series": "weeks_of_cover",
        "reason": ("Cover needs a demand forecast. The only forecast this data "
                   "supports is 'what sold recently, repeated', which would "
                   "print as a projection without being one. Recent sold "
                   "quantity is shown instead, unprojected."),
    }]
    # Asked for, and refused, with the reason. Each of these is either a
    # forecast this data cannot support or a field Zoho does not give us —
    # and inventing either would put a number on the screen that looks like
    # the measured ones beside it.
    out.extend([
        {
            "series": "recovery_probability",
            "reason": ("How likely a dead line is to sell needs a model of "
                       "future demand. The only one this data supports is "
                       "'what sold before, repeated', which would print as a "
                       "probability without being one. The last sale date and "
                       "the customers who bought it are shown instead — that "
                       "is the evidence a person would use to make the same "
                       "call, unlaundered."),
        },
        {
            "series": "expected_recovery_value",
            "reason": ("A probability times a price. Both halves are guesses "
                       "here: the probability is not computable (above) and "
                       "the clearing price is whatever the discount turns out "
                       "to be. What the line costs to keep each month is shown "
                       "instead, which is a fact."),
        },
        {
            "series": "branch",
            "reason": ("Stock is read per Zoho company, not per warehouse. "
                       "Zoho reports location-level stock only on the "
                       "Inventory plan's warehouse endpoints, which this pull "
                       "does not read. Each connected company is effectively "
                       "one branch until it does."),
        },
        {
            "series": "supplier_and_brand",
            "reason": ("The item master carries no brand or vendor field on "
                       "this book, and a bill's supplier belongs to the bill "
                       "rather than to the item — an item bought from two "
                       "suppliers has no single one. Attributing the most "
                       "recent would be a guess that reads as a fact."),
        },
    ])
    if no_policy:
        out.append({
            "series": "reorder_point",
            "reason": (f"{no_policy} of {total} items have no reorder level set "
                       "in Zoho. They cannot be below a point that does not "
                       "exist, so they are excluded from that group rather "
                       "than assumed to reorder at zero."),
        })
    return out
