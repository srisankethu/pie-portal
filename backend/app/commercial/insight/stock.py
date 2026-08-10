"""What is on the shelf, and which of it is a problem.

Four questions. Three were always answerable from recorded facts, and the fourth
splits in two — the measurement is answerable and the projection is not, which is
the distinction the last section draws:

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

**Cover, as a measurement rather than a projection.** ``days_of_cover`` is on
every row: what is on the shelf divided by the rate this item has actually moved
at since it first sold. A ratio of two recorded facts, and quantity only — no
cost term, so it is safe on the one screen where the purchase rate is withheld.

What stays refused is *projected* cover — how long the stock will last. That
needs a demand forecast, and the honest forecast from this data is "what sold
recently, repeated", which reads like a projection without being one. The
distinction is the whole of it: "you hold 240 days at the rate this has moved"
is arithmetic over the past; "this will last 240 days" is a claim about the
future. The refusal is still named in the response, restated to say which of the
two it refuses — see ``_unavailable``.

This paragraph used to say cover was absent altogether, which was true when the
only reading on offer was a trailing window. The excess-cover decision card had
already been dividing on the honest reading for as long as it has existed, so
the screen was refusing a number the queue was printing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Optional

from .. import offtake
from . import absence

#: Health bands, worst last. The screen colours on these and nothing else, so
#: "amber" means one thing across every surface rather than one thing per chart.
#:
#: ``UNKNOWN`` is not a fourth severity — it sits outside the ordering. It means
#: the line has never sold *and the platform cannot show it has had time to*.
#: See ``StockLine.health`` for why that is a band rather than a shade of DEAD.
HEALTHY, SLOW, DEAD, UNKNOWN = "HEALTHY", "SLOW", "DEAD", "UNKNOWN"

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
    #: Has the carrying rate been published — in a policy document, a training
    #: deck, an email, anywhere a salesperson could read it?
    #:
    #: This is the guard for the disclosure hazard documented on ``to_dict``.
    #: ``monthly = quantity x cost x rate / 12``, and the quantity is on the
    #: row, so the rate is the *only* thing standing between a salesperson and
    #: every purchase cost in the catalogue. While it is secret the monthly
    #: drain is safe to show; the moment it is not, the column has to come off
    #: that screen.
    #:
    #: It was a paragraph of prose asking somebody to remember. It is now a
    #: setting the code reads, so remembering is not required — flip it and the
    #: column disappears for every role that cannot already see cost.
    rate_is_published: bool = False

    @property
    def monthly_pct(self) -> float:
        return self.annual_pct / 12.0

    def drain_visible_to(self, *, with_cost: bool) -> bool:
        """May this reader see what a line costs to keep each month?

        Always, for a reader who can already see cost — they can compute it
        anyway. For anyone else, only while the rate is unpublished.
        """
        return with_cost or not self.rate_is_published


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
    #: The earliest date the platform has any evidence this line existed —
    #: first stock observation or first purchase, whichever came first.
    #:
    #: This is the denominator "it has never sold" needs. Measured on the live
    #: book: every item record in the SLS master was created inside the last
    #: six months, and 81% of the value the DEAD band would have claimed was
    #: stock bought in the previous ten weeks. ``None`` where even this cannot
    #: be established, which is itself a reason to withhold the verdict rather
    #: than to fall through to the worst one.
    first_seen: Optional[date] = None
    #: The first sale on record. The *other* end of the offtake window, and a
    #: different fact from ``first_seen``: how long the platform has watched the
    #: line says whether "never sold" is a finding, while how long it has been
    #: *selling* is the only honest denominator for a rate.
    #:
    #: Quantity-side and never gated on cost — it is a date on which a sale
    #: happened, the same class of fact as ``last_sold``.
    first_sold: Optional[date] = None

    # ── how fast it moves, and how long the shelf covers that ────────────────
    #
    # Quantity only. Both figures below go to every role, including the one that
    # cannot see ``purchase_rate`` — see ``commercial/offtake.py`` for why that
    # is safe and ``to_dict`` for the boundary it sits beside.

    def daily_offtake(self, as_of: date) -> Optional[float]:
        """Units leaving the shelf per day, at the rate actually recorded."""
        rate = offtake.daily_offtake(self.sold_qty_window, self.first_sold, as_of)
        return None if rate is None else float(rate)

    def days_of_cover(self, as_of: date) -> Optional[float]:
        """How many days of measured offtake the current shelf amounts to.

        ``None`` for a line that has never sold: no rate, therefore no cover.
        Not zero (which would read as "we are out of this") and not an infinity
        (which would sort a never-sold line to the top of a "most cover" list) —
        ``CLAUDE.md`` §1, absence of evidence is not a pass in either direction.

        Whole days. A cover figure carrying two decimals would imply a precision
        that "units sold since this first moved" does not have, which is the same
        reason ``offtake.DAYS_PER_MONTH`` is 30 rather than 30.4.
        """
        days = offtake.days_of_cover(self.on_hand, self.sold_qty_window,
                                     self.first_sold, as_of)
        return None if days is None else float(round(days))

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
        """Healthy, slow, dead — or unknown. Age alone; value does not make
        stock fresh.

        The one subtlety, and it was a real defect rather than a refinement:
        **a line that has never sold is not thereby dead.** It is dead only if
        it has never sold *and* has been on the books long enough that never
        selling is a finding. Those are different claims and the fold now
        carries the fact that separates them (``first_seen``).

        This used to read ``if days is None or days >= c.dead_days: return
        DEAD``, which turned a missing sale date into the worst verdict on the
        screen. On the live SLS book that banded ₹10.6 lakh of stock bought in
        the previous ten weeks as DEAD and proposed writing it off — a 5.3x
        overstatement of the dead-stock figure, every rupee of it in the
        direction of telling the owner to discard stock they had just paid for.

        It is the ``CLAUDE.md`` §1 rule with the sign flipped: absence of
        evidence became evidence of a problem instead of a pass. Either way the
        honest answer is UNKNOWN, and the tell is the same one §1 names — a
        ``None`` folded into the same branch as a real measurement.
        """
        if self.on_hand <= 0:
            return HEALTHY
        days = self.idle_days(as_of)
        if days is not None:
            if days >= c.dead_days:
                return DEAD
            return SLOW if days >= c.slow_days else HEALTHY
        # Never sold. Whether that is a finding depends entirely on how long
        # there has been to sell it.
        opportunity = self.opportunity_days(as_of)
        if opportunity is None or opportunity < c.dead_days:
            return UNKNOWN
        return DEAD

    def opportunity_days(self, as_of: date) -> Optional[int]:
        """How long this line has had the chance to sell.

        Days since the platform first saw the item — its first stock reading or
        its first purchase, whichever is earlier. ``None`` when neither is on
        record, which means the question "has it had time to sell?" is not
        answerable and the band must say so.
        """
        return (as_of - self.first_seen).days if self.first_seen else None

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
        if band in (HEALTHY, UNKNOWN):
            # UNKNOWN is stock we cannot yet say anything about. "Do nothing"
            # is the only honest instruction, and it is emphatically not a
            # write-off: this is the branch that was proposing the owner
            # discard stock bought the month before.
            return "HOLD"
        if band == SLOW:
            return "PUSH"
        days = self.idle_days(as_of)
        if days is None:
            # Never sold, and on the books longer than the dead threshold —
            # ``health`` has already established the second half. Nothing about
            # a discount is evidenced here; it is a question for whoever
            # bought it.
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

    def idle(self, as_of: date, c: Carrying) -> bool:
        """On the shelf and not moving, by this organization's own threshold.

        Takes ``Carrying`` rather than reading a module constant. It used to do
        the latter while ``health`` read ``c.slow_days``, so one idea had two
        owners and they agreed only because the defaults happened to match — an
        owner who set ``CI_SLOW_STOCK_DAYS=90`` got a screen whose IDLE group,
        SLOW band and group heading disagreed, and the heading still said 180.
        That is the responsibility duplication ``CLAUDE.md`` §2 names.

        A line whose band is UNKNOWN is not idle. It is unmeasured, and putting
        it in a group headed "nothing sold in N days" would assert exactly the
        thing the band exists to withhold.
        """
        if self.on_hand <= 0:
            return False
        band = self.health(as_of, c)
        if band == UNKNOWN:
            return False
        days = self.idle_days(as_of)
        # Never sold, with the opportunity window already established by
        # ``health``, is the strongest version of the finding — not a reason to
        # exclude the row.
        return days is None or days >= c.slow_days

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
        screen for any role below owner. And the remaining hazard — that the
        rate is published somewhere this code cannot see — is now a *setting*
        rather than a paragraph asking somebody to remember:
        ``Carrying.rate_is_published``. Turn it on and this column comes off
        the salesperson's screen automatically, along with the two figures
        derived from it, and the screen says why instead of going quietly
        blank.

        The alternative was to withhold the drain from a salesperson always,
        and that number is the entire point of the screen — so the coupling is
        enforced rather than the feature removed.

        ``days_of_cover`` sits beside that boundary and does **not** move it,
        which is worth stating because the paragraph above is the reason to check
        rather than to assume. Cover is ``on_hand x observed_days /
        units_sold`` — three quantities and a pair of dates, with no cost term
        anywhere in it. There is nothing in it to combine with the drain that
        was not already on the row: ``on_hand`` and ``sold_qty_window`` are both
        published fields today, and ``first_sold`` is the date of a sale, which
        is the same class of fact as ``last_sold`` beside it. So cover is an
        operations number that answers a quantity question, not a new figure
        whose purpose is economics — the test ``CLAUDE.md`` §1 sets for a field
        like this.
        """
        band = self.health(as_of, c)
        out = {
            "product_id": self.product_id, "label": self.label,
            "on_hand": self.on_hand, "available": self.available,
            "actual_available": self.actual_available,
            "reorder_level": self.reorder_level,
            "last_sold": self.last_sold.isoformat() if self.last_sold else None,
            "idle_days": self.idle_days(as_of),
            # How long the line has had to sell. On a row banded UNKNOWN this
            # is the number that explains the band, so it is never withheld —
            # it is a date arithmetic, not economics.
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "opportunity_days": self.opportunity_days(as_of),
            "sold_qty_window": round(self.sold_qty_window, 2),
            # How long the shelf lasts at the rate this has actually moved, and
            # the date the window measuring that rate opens on. Quantity and
            # dates only, so both go to every role — the reasoning is in the
            # boundary note above and in ``commercial/offtake.py``.
            "first_sold": self.first_sold.isoformat() if self.first_sold else None,
            "days_of_cover": self.days_of_cover(as_of),
            "oversold": self.oversold,
            "below_reorder": self.below_reorder,
            "idle": self.idle(as_of, c),
            # Age, health and what it costs to keep — operational, and the
            # three things a decision about clearing stock is made from.
            "health": band,
            "priority": self.priority(as_of, c),
            "action": self.recommended_action(as_of, c),
            "action_label": ACTIONS[self.recommended_action(as_of, c)],
            # Who has bought it before. The answer to "who do I call about
            # this", which is what turns a dead-stock row into a phone call.
            "buyers": list(self.buyers[:6]),
        }
        # Absent, not zeroed, when the rate has been published and this reader
        # cannot already see cost: a zero would read as "this costs nothing to
        # keep", which is the opposite of true.
        if c.drain_visible_to(with_cost=with_cost):
            out["monthly_holding_cost"] = self.monthly_holding_cost(c)
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


def lines_from_state(states: dict[str, dict[str, Any]], *,
                     labels: dict[str, str],
                     buyers: dict[str, tuple[str, ...]],
                     with_cost: bool) -> list[StockLine]:
    """Turn folded INVENTORY state into the rows this module reasons about.

    A projection, not a calculation: every number here was already computed by
    the state fold, and this puts it in the shape ``build`` reads. That is the
    point of the exercise — the arithmetic below is correct and tested, and it
    moves rather than gets rewritten.

    Pure, and it takes dictionaries rather than a session, so this module stays
    a set of functions of its inputs and the router keeps the database.

    ``with_cost`` gates ``last_purchased`` and nothing else. The purchase rate
    is always set, because what a line costs to *keep* each month is derived
    from it and that figure is on every role's screen — it is the number this
    screen exists for. What leaves is ``to_dict``'s decision, as it already was;
    withholding the rate here instead silently zeroed a salesperson's monthly
    cash drain, which a test that only checked the field was present did not
    notice and a browser did.
    """
    rows: list[StockLine] = []
    for product_id, value in states.items():
        # A service has no shelf. Counting it as zero on hand would put the
        # whole service catalogue in the out-of-stock list forever. Absent
        # means the observation predates the flag, and the safe reading of
        # "we do not know" is the one that keeps the row visible.
        if value.get("tracked") is False:
            continue
        if "on_hand" not in value:
            # Nothing has ever observed this item's shelf — it has sales or
            # cost history and no stock reading. Not a zero.
            continue
        rows.append(StockLine(
            product_id=product_id,
            label=labels.get(product_id) or f"Unnamed item (id {product_id})",
            on_hand=_number(value.get("on_hand")) or 0.0,
            available=_number(value.get("available")),
            actual_available=_number(value.get("actual_available")),
            reorder_level=_number(value.get("reorder_level")),
            last_sold=_day(value.get("last_sold_on")),
            sold_qty_window=_number(value.get("units_sold")) or 0.0,
            purchase_rate=_number(value.get("purchase_rate")),
            # Owner zone: paired with the purchase rate it is a supplier's
            # price on a date, so it is not even read for other roles.
            last_purchased=(_day(value.get("last_purchased_on")) if with_cost else None),
            buyers=buyers.get(product_id, ()),
            # Earliest evidence the item existed, from either side of the
            # fold. Not gated on ``with_cost``: the health band is computed
            # from it and every role sees the band, so withholding it here
            # would silently move a salesperson's rows into a different
            # bucket from an owner's — the same shape of bug as the drain
            # that this function's docstring already records.
            first_seen=_earliest(_day(value.get("first_observed_on")),
                                 _day(value.get("first_purchased_on"))),
            # The offtake window's opening date, MINed by the same fold that
            # MAXes ``last_sold_on``. Read from state rather than derived here:
            # the excess-cover detector divides by a rate measured over exactly
            # this window, and re-deriving the denominator on this side is how
            # the screen and the decision queue would come to disagree.
            first_sold=_day(value.get("first_sold_on")),
        ))
    return rows


def _earliest(*days: Optional[date]) -> Optional[date]:
    """The earliest of the dates that are actually present, or None."""
    known = [d for d in days if d is not None]
    return min(known) if known else None


def _number(raw: Any) -> Optional[float]:
    """A state value back to a float. State stores money and quantity as
    strings so the fold is exact; this module's arithmetic is float, and the
    conversion happens once, here, at the boundary."""
    if raw is None or raw == "":
        return None
    return float(raw)


def _day(raw: Any) -> Optional[date]:
    return date.fromisoformat(str(raw)) if raw else None


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
    idle = [r for r in rows if r.idle(as_of, c) and not r.oversold]
    no_policy = [r for r in rows if r.reorder_level is None]
    unknown = [r for r in rows if r.on_hand > 0
               and r.health(as_of, c) == UNKNOWN]

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
            "label": f"On the shelf, nothing sold in {c.slow_days} days",
            "meaning": ("Measured, not judged. The last sale date is attached so "
                        "the call about whether it is dead stock stays with a "
                        "person who knows the line."),
            "items": [r.to_dict(as_of, c, with_cost=with_cost) for r in idle[:40]],
            "count": len(idle),
        },
        {
            "key": "UNKNOWN",
            "label": "Too new to judge",
            "meaning": ("On the shelf, never sold, and not on the books long "
                        f"enough for that to mean anything — under {c.dead_days} "
                        "days since the platform first saw it. Not dead stock: "
                        "there has not yet been time for it to be. Shown as its "
                        "own group rather than folded into the dead list, "
                        "because that is where it used to sit and it was the "
                        "largest thing in it."),
            "items": [r.to_dict(as_of, c, with_cost=with_cost) for r in unknown[:40]],
            "count": len(unknown),
        },
    ]

    counts = {
        "tracked_items": len(rows),
        "oversold": len(oversold),
        "below_reorder": len(below),
        "idle": len(idle),
        "no_reorder_point": len(no_policy),
        "on_hand_items": sum(1 for r in rows if r.on_hand > 0),
        "too_new_to_judge": len(unknown),
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
        "idle_after_days": c.slow_days,
        "dead_after_days": c.dead_days,
        "slow_after_days": c.slow_days,
        "unavailable": _unavailable(
            len(no_policy), len(rows),
            drain_withheld=not c.drain_visible_to(with_cost=with_cost),
            too_new=len(unknown),
            # Counted over the shelf rather than the whole book: a line with no
            # stock has no cover to report either way, and including it would
            # inflate a number whose only job is to explain the blanks a reader
            # can actually see in the grid.
            no_offtake=sum(1 for r in on_shelf if r.days_of_cover(as_of) is None),
            dead_days=c.dead_days),
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
    {"key": "UNKNOWN", "label": "Too new to judge", "field": "health",
     "op": "eq", "value": UNKNOWN},
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
    cards: list[dict] = []
    # The headline card is the drain, and it goes only to a reader allowed to
    # see it. Once the carrying rate is published, the drain plus the quantity
    # on each row recovers the purchase cost — so the card comes off with the
    # column rather than staying as a total nobody can break down.
    if c.drain_visible_to(with_cost=with_cost):
        cards.append(
            {"key": "MONTHLY_DRAIN", "label": "Costing us every month",
             "value": round(monthly, 2), "unit": "money", "filter": None,
             "note": "What the shelf costs to keep, this month."})
    if with_cost:
        cards.append(
            {"key": "ANNUAL_DRAIN", "label": "Over a year",
             "value": round(monthly * 12, 2), "unit": "money", "filter": None,
             "note": "The monthly figure at the current carrying rate."})
    if c.drain_visible_to(with_cost=with_cost):
        cards.append(
            {"key": "DEAD_DRAIN", "label": f"From stock idle {c.dead_days}+ days",
             "value": round(dead_monthly, 2), "unit": "money", "filter": "DEAD",
             "note": "The part of that drain nothing is currently selling."})
    cards.append(
        {"key": "DEAD_COUNT", "label": "Lines gone quiet",
         "value": len(dead), "unit": "count", "filter": "DEAD",
         "note": f"On the shelf with no sale in {c.dead_days} days."})
    # Counted and shown rather than left out. These rows used to be inside the
    # DEAD figures above, so a reader comparing this screen against last
    # quarter's needs to see where they went — a total that quietly shrinks is
    # the kind of change that gets reported as a bug.
    too_new = [r for r in rows if r.health(as_of, c) == UNKNOWN]
    if too_new:
        cards.append(
            {"key": "TOO_NEW", "label": "Too new to judge",
             "value": len(too_new), "unit": "count", "filter": "UNKNOWN",
             "note": ("On the shelf, never sold, and not here long enough for "
                      "that to be a finding.")})
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


def _unavailable(no_policy: int = 0, total: int = 0, *,
                 drain_withheld: bool = False, too_new: int = 0,
                 no_offtake: int = 0, dead_days: int = 365) -> list[dict]:
    out: list[dict] = []
    if too_new:
        out.append({
            "series": "health_band_for_new_stock",
            # TRANSIENT: nobody can do anything about this and nobody should
            # try. Each of these lines either sells or crosses `dead_days`, and
            # the answer arrives on its own either way.
            "kind": absence.TRANSIENT,
            "reason": (f"{too_new} line(s) on the shelf have never sold and "
                       f"have been on the books less than {dead_days} days. "
                       "Whether they are dead stock is not yet knowable, so "
                       "they are banded UNKNOWN rather than DEAD. They were "
                       "previously counted as dead — on this book that was "
                       "the majority of the dead-stock figure, and it "
                       "proposed writing off stock that had just been "
                       "bought."),
        })
    if drain_withheld:
        # Named rather than left blank. A column that vanishes with no
        # explanation reads as a bug, and the next person to notice will file
        # one — or worse, put it back.
        out.append({
            "series": "monthly_cash_drain",
            # WITHHELD, not PERMANENT: the figure is computed and correct, and
            # an owner sees it. Filing it under "not answerable" is how a
            # working permission rule gets "fixed".
            "kind": absence.WITHHELD,
            "reason": ("What a line costs to keep is quantity x cost x the "
                       "carrying rate / 12. The quantity is on every row, so "
                       "once the carrying rate is public the drain gives away "
                       "the purchase cost of everything in the catalogue. The "
                       "rate has been marked published, so this column is off "
                       "for roles that cannot already see cost. An owner can "
                       "still see it."),
        })
    # Restated, not deleted, and narrowed to the half that is still true — the
    # same treatment ``supplier_and_brand`` below got, for the same reason. What
    # this entry refused was cover *as a projection*: how long the stock will
    # last, which needs a demand forecast this data cannot support. It read as a
    # refusal of the whole idea of cover, and on that reading the screen was
    # refusing a figure the excess-cover decision card had been printing all
    # along from the same two fold fields. A screen and a queue that disagree
    # about whether a number is knowable teach a reader to trust neither.
    #
    # PERMANENT still: no amount of extra data makes a forecast honest here. It
    # is the *series* that narrowed, not the kind.
    out.append({
        "series": "weeks_of_cover",
        "kind": absence.PERMANENT,
        "reason": ("Projected cover — how long this stock will last — needs a "
                   "demand forecast, and the only one this data supports is "
                   "'what sold recently, repeated', which would print as a "
                   "projection without being one. What is shown instead is "
                   "measured, on every row: days of cover at the rate the item "
                   "has actually moved at since it first sold. That is a ratio "
                   "of two recorded facts and it is not a prediction — it says "
                   "how much you hold, not how long it will take to go."),
    })
    if no_offtake:
        out.append({
            "series": "days_of_cover_for_lines_that_have_never_sold",
            # TRANSIENT rather than PERMANENT: a sale makes the rate computable,
            # so data would fix it — and rather than COLLECTABLE, because there
            # is nothing for anyone to go and record. Nobody should act on the
            # missing figure; the line's own health band and action are the
            # actionable part, and they are already on the row.
            "kind": absence.TRANSIENT,
            "reason": (f"{no_offtake} line(s) on the shelf have never sold, so "
                       "they have no offtake rate and no days of cover. Left "
                       "blank rather than filled in: zero would read as 'we are "
                       "out of this' and an infinity would sort them to the top "
                       "of the most-covered list. What to do about them is the "
                       "health band's question, not cover's."),
        })
    # Asked for, and refused, with the reason. Each of these is either a
    # forecast this data cannot support or a field Zoho does not give us —
    # and inventing either would put a number on the screen that looks like
    # the measured ones beside it.
    out.extend([
        {
            "series": "recovery_probability",
            "kind": absence.PERMANENT,
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
            "kind": absence.PERMANENT,
            "reason": ("A probability times a price. Both halves are guesses "
                       "here: the probability is not computable (above) and "
                       "the clearing price is whatever the discount turns out "
                       "to be. What the line costs to keep each month is shown "
                       "instead, which is a fact."),
        },
        {
            "series": "branch",
            # BUILDABLE, not PERMANENT: the endpoint exists and is on a plan.
            # This one is a purchase and a puller, not a limit.
            "kind": absence.BUILDABLE,
            "reason": ("Stock is read per Zoho company, not per warehouse. "
                       "Zoho reports location-level stock only on the "
                       "Inventory plan's warehouse endpoints, which this pull "
                       "does not read. Each connected company is effectively "
                       "one branch until it does."),
        },
        {
            "series": "supplier_and_brand",
            # PERMANENT on the surviving reason, which is a grain mismatch
            # rather than a gap: more data does not give a stock level one
            # supplier. Note it was *not* permanent for the reason it used to
            # give — see the comment below.
            "kind": absence.PERMANENT,
            # Narrowed, not deleted. This used to say the item master carries no
            # maker field at all, which stopped being true when the item pull
            # started reading ``manufacturer`` — and a screen that refuses on a
            # reason the platform has since fixed teaches people to ignore its
            # refusals. What survives is the part that is still true: stock is a
            # level, and the two facts that could attribute it disagree about
            # *when*.
            "reason": ("Stock is what is on the shelf now, and neither fact "
                       "that names a principal is a fact about now: the item "
                       "master's manufacturer describes the item rather than "
                       "the batch, and a bill's supplier belongs to the bill — "
                       "an item bought from two suppliers has no single one. "
                       "Splitting a level between them would read as a "
                       "measurement. The mix and dependency screens attribute "
                       "*trade*, where a date makes the question answerable."),
        },
    ])
    if no_policy:
        out.append({
            "series": "reorder_point",
            # The clearest COLLECTABLE in the package: a named count of rows,
            # each fixable by one person typing one number.
            "kind": absence.COLLECTABLE,
            "reason": (f"{no_policy} of {total} items have no reorder level set "
                       "in Zoho. They cannot be below a point that does not "
                       "exist, so they are excluded from that group rather "
                       "than assumed to reorder at zero."),
        })
    return out
