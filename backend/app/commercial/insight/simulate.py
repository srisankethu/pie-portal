"""Scenario arithmetic over persisted facts — never a forecast in disguise.

A simulator is the easiest place in a commercial platform to start lying. Ask it
"what if I raise prices 5%?" and the tempting answer is a single confident
number, which requires guessing how many customers would leave — a guess this
platform has no basis for and no way to show its working on.

So the contract here is narrower and honest:

**Mechanical effects are computed.** A 5% price rise on a known volume at a
known cost produces an exactly determined revenue and margin, and that
arithmetic is shown in full.

**Behavioural effects are elicited, never invented.** Volume response is an
input the user sets, and the response says so. The output is "at your assumed
−10% volume this earns X" rather than "this will earn X". Where the assumption
dominates the result, the response says that too.

**Break-even is the honest headline.** The most useful thing a price simulator
can say is not a projection but a boundary: *how much volume you can lose before
this is worse than doing nothing.* That number is fully determined by cost,
price and margin, needs no behavioural guess, and is what a distributor actually
negotiates against.

Both of the scenarios this module used to declare blocked are now implemented.
They were blocked on data — open purchase orders and stock levels — and the
Business State work read both. What remains blocked is *narrower* and is stated
as such rather than left as the old, now-stale sentence: a supplier delay
cannot be attributed to individual items, because purchase orders are read at
header grain and nothing links a delayed order to the shelf it would have
filled.

That distinction matters. "We cannot do supplier delay" was true and is no
longer; "we cannot say which items run short" is true now, and a reader who
believes the first will stop asking for the second.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models

PRICE_CHANGE = "PRICE_CHANGE"
CUSTOMER_RECOVERY = "CUSTOMER_RECOVERY"
MARGIN_FLOOR = "MARGIN_FLOOR"
INVENTORY_CHANGE = "INVENTORY_CHANGE"
SUPPLIER_DELAY = "SUPPLIER_DELAY"

#: What is still refused, and precisely what for. Narrower than it was, because
#: the data that blocked two whole scenarios now exists — leaving the old
#: sentence up would tell a reader the platform cannot do something it can.
UNAVAILABLE: tuple[dict[str, str], ...] = (
    {"scenario": "SUPPLIER_DELAY_BY_ITEM",
     "needs": "purchase order lines",
     "why": "Purchase orders are read at header grain — what was ordered from "
            "whom, for how much, and how much is still to come, but not which "
            "items. So a delay can be costed in cash and in commitment age, "
            "and cannot be attributed to the shelf it would have filled. "
            "Reading PO lines would cost one API call per order."},
    {"scenario": "SUPPLIER_DELAY_AGAINST_PROMISE",
     "needs": "promised delivery dates",
     "why": "``expected_delivery_date`` is blank on effectively every order in "
            "this book, so a delay is measured from today rather than against "
            "a date somebody committed to. The scenario says how much later, "
            "not how much late."},
)


@dataclass
class Line:
    """One customer × item relationship, as the simulator sees it."""

    customer_id: str
    product_id: str
    customer_label: str
    product_label: str
    revenue_12m: float
    qty_recent: float
    unit_price: Optional[float]
    unit_cost: Optional[float]
    current_margin: Optional[float]

    @property
    def priced(self) -> bool:
        return bool(self.unit_price and self.unit_cost and self.qty_recent)


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def load_lines(session: Session, org: str, *, customer_id: Optional[str] = None,
               product_id: Optional[str] = None,
               customer_names: Optional[dict] = None,
               product_names: Optional[dict] = None) -> list[Line]:
    stmt = select(models.CustomerItemMetric).where(
        models.CustomerItemMetric.organization_id == org)
    if customer_id:
        stmt = stmt.where(models.CustomerItemMetric.customer_id == customer_id)
    if product_id:
        stmt = stmt.where(models.CustomerItemMetric.product_id == product_id)

    names_c = customer_names or {}
    names_p = product_names or {}
    return [
        Line(customer_id=r.customer_id, product_id=r.product_id,
             customer_label=names_c.get(r.customer_id, r.customer_id),
             product_label=names_p.get(r.product_id, r.product_id),
             revenue_12m=_f(r.revenue_12m) or 0.0,
             qty_recent=_f(r.qty_recent) or 0.0,
             unit_price=_f(r.current_sell_price),
             unit_cost=_f(r.current_effective_cost),
             current_margin=_f(r.current_margin))
        for r in session.scalars(stmt).all()
    ]


def break_even_volume_change(price_change_pct: float, margin: float) -> Optional[float]:
    """How much volume can be lost before a price rise stops being worth it.

    Contribution per unit before is ``m``; after a price change of ``p`` it is
    ``m + p`` (as a share of the original price). Holding total contribution
    equal gives the volume multiplier ``m / (m + p)``, so the tolerable change
    is that minus one.

    Returns None when the arithmetic has no meaning — a price cut that takes
    contribution to zero or below cannot be compensated by any volume.
    """
    after = margin + price_change_pct
    if after <= 0:
        return None
    return (margin / after) - 1.0


def price_change(lines: Iterable[Line], *, pct: float,
                 assumed_volume_change: float = 0.0) -> dict:
    """Apply a price change to every priced line and show the arithmetic."""
    rows = [ln for ln in lines if ln.priced]
    skipped = [ln for ln in lines if not ln.priced]

    base_revenue = base_profit = new_revenue = new_profit = 0.0
    per_line = []
    for ln in rows:
        qty = ln.qty_recent
        new_qty = qty * (1.0 + assumed_volume_change)
        new_price = (ln.unit_price or 0.0) * (1.0 + pct)

        b_rev = (ln.unit_price or 0.0) * qty
        b_prof = b_rev - (ln.unit_cost or 0.0) * qty
        n_rev = new_price * new_qty
        n_prof = n_rev - (ln.unit_cost or 0.0) * new_qty

        base_revenue += b_rev
        base_profit += b_prof
        new_revenue += n_rev
        new_profit += n_prof

        margin = ln.current_margin if ln.current_margin is not None else (
            (b_prof / b_rev) if b_rev else 0.0)
        per_line.append({
            "customer_id": ln.customer_id, "product_id": ln.product_id,
            "customer_label": ln.customer_label, "product_label": ln.product_label,
            "unit_price": round(ln.unit_price or 0.0, 2),
            "new_unit_price": round(new_price, 2),
            "current_margin": round(margin, 4),
            "new_margin": round((n_prof / n_rev), 4) if n_rev else None,
            "revenue_delta": round(n_rev - b_rev, 2),
            "profit_delta": round(n_prof - b_prof, 2),
            "break_even_volume_change": (
                round(be, 4) if (be := break_even_volume_change(pct, margin)) is not None
                else None),
        })

    per_line.sort(key=lambda r: abs(r["profit_delta"]), reverse=True)
    blended = (base_profit / base_revenue) if base_revenue else 0.0
    break_even = break_even_volume_change(pct, blended)

    return {
        "scenario": PRICE_CHANGE,
        "inputs": {"price_change_pct": pct,
                   "assumed_volume_change": assumed_volume_change},
        "baseline": {"revenue": round(base_revenue, 2),
                     "gross_profit": round(base_profit, 2),
                     "margin": round(blended, 4) if base_revenue else None},
        "projected": {"revenue": round(new_revenue, 2),
                      "gross_profit": round(new_profit, 2),
                      "margin": round(new_profit / new_revenue, 4) if new_revenue else None},
        "delta": {"revenue": round(new_revenue - base_revenue, 2),
                  "gross_profit": round(new_profit - base_profit, 2)},
        # The headline that needs no behavioural assumption at all.
        "break_even_volume_change": round(break_even, 4) if break_even is not None else None,
        "assumption_note": (
            "Volume response is your assumption, not a prediction. The platform "
            "has no basis for how customers react to a price change. The "
            "break-even figure above needs no assumption: it is how much volume "
            "you could lose before this change stops paying for itself."),
        "lines": per_line[:100],
        "line_count": len(rows),
        "skipped_lines": len(skipped),
        "skipped_reason": ("Lines without a current price, cost or recent quantity "
                           "cannot be simulated and are excluded rather than "
                           "assumed." if skipped else None),
    }


def margin_floor(lines: Iterable[Line], *, floor: float) -> dict:
    """What lifting every line to a minimum margin would be worth.

    Deterministic and assumption-free on the money side: raising a line's margin
    to the floor at the same volume has an exactly computable effect. What it
    does *not* model is whether the customer accepts it — which is why the
    output is framed as exposure, not gain.
    """
    rows = [ln for ln in lines if ln.priced and ln.current_margin is not None]
    below = [ln for ln in rows if (ln.current_margin or 0) < floor]

    uplift = 0.0
    detail = []
    for ln in below:
        qty = ln.qty_recent
        current_rev = (ln.unit_price or 0.0) * qty
        needed_price = (ln.unit_cost or 0.0) / (1.0 - floor) if floor < 1 else None
        if needed_price is None:
            continue
        gain = (needed_price - (ln.unit_price or 0.0)) * qty
        uplift += gain
        detail.append({
            "customer_id": ln.customer_id, "product_id": ln.product_id,
            "customer_label": ln.customer_label, "product_label": ln.product_label,
            "current_margin": round(ln.current_margin or 0.0, 4),
            "unit_price": round(ln.unit_price or 0.0, 2),
            "price_at_floor": round(needed_price, 2),
            "price_increase_pct": round(
                (needed_price / ln.unit_price - 1.0), 4) if ln.unit_price else None,
            "revenue_at_risk": round(current_rev, 2),
            "profit_gain": round(gain, 2),
        })

    detail.sort(key=lambda r: r["profit_gain"], reverse=True)
    return {
        "scenario": MARGIN_FLOOR,
        "inputs": {"floor": floor},
        "lines_below_floor": len(below),
        "lines_considered": len(rows),
        "revenue_at_risk": round(sum(d["revenue_at_risk"] for d in detail), 2),
        "profit_gain_if_all_lifted": round(uplift, 2),
        "assumption_note": (
            "Every line lifted to the floor at unchanged volume. That is an "
            "upper bound on the money, not a forecast — it assumes every "
            "customer accepts the new price."),
        "lines": detail[:100],
    }


def customer_recovery(lines: Iterable[Line], *, customer_ids: list[str],
                      recovery_share: float = 1.0) -> dict:
    """What winning back specific customers is worth, at their own past rates.

    Uses each customer's *own* trailing revenue rather than an average, because
    an average recovery is a number about nobody. ``recovery_share`` scales it
    for a partial win-back and is the only assumption in play.
    """
    wanted = set(customer_ids)
    rows = [ln for ln in lines if ln.customer_id in wanted]

    by_customer: dict[str, dict] = {}
    for ln in rows:
        entry = by_customer.setdefault(ln.customer_id, {
            "customer_id": ln.customer_id, "label": ln.customer_label,
            "revenue_12m": 0.0, "gross_profit_12m": 0.0})
        entry["revenue_12m"] += ln.revenue_12m
        if ln.current_margin is not None:
            entry["gross_profit_12m"] += ln.revenue_12m * ln.current_margin

    recovered = []
    for entry in by_customer.values():
        recovered.append({
            **{k: (round(v, 2) if isinstance(v, float) else v)
               for k, v in entry.items()},
            "recovered_revenue": round(entry["revenue_12m"] * recovery_share, 2),
            "recovered_profit": round(entry["gross_profit_12m"] * recovery_share, 2),
        })
    recovered.sort(key=lambda r: r["recovered_revenue"], reverse=True)

    return {
        "scenario": CUSTOMER_RECOVERY,
        "inputs": {"customer_ids": list(customer_ids),
                   "recovery_share": recovery_share},
        "recovered_revenue": round(sum(r["recovered_revenue"] for r in recovered), 2),
        "recovered_profit": round(sum(r["recovered_profit"] for r in recovered), 2),
        "assumption_note": (
            "Each customer valued at their own trailing twelve months, scaled by "
            "the recovery share you set. It is what the relationship was worth, "
            "not a probability that it returns."),
        "customers": recovered,
    }


# ── inventory change ─────────────────────────────────────────────────────────
#
# The question a distributor actually asks about dead stock is not "will it
# sell" — nothing here can answer that — but "what do I get back, and what do I
# stop paying, if I move it?" Both halves are arithmetic over facts already
# folded into INVENTORY state.
#
# The behavioural half is elicited exactly as it is for a price change: the
# share that moves and the discount it takes are the user's assumptions, and
# the response says so rather than absorbing them into a headline.

@dataclass
class ShelfLine:
    """One item on the shelf, as the simulator sees it.

    Built from folded INVENTORY state by the caller, so this module stays a set
    of functions of its inputs — the same arrangement ``load_lines`` has with
    ``CustomerItemMetric`` and ``stock.lines_from_state`` has with state.
    """

    product_id: str
    label: str
    on_hand: float
    purchase_rate: Optional[float]
    idle_days: Optional[int]

    @property
    def priced(self) -> bool:
        return bool(self.purchase_rate and self.on_hand > 0)

    @property
    def capital(self) -> float:
        """What this line cost to buy, for the quantity still held."""
        return (self.on_hand * (self.purchase_rate or 0.0)) if self.priced else 0.0


def break_even_idle_months(discount: float,
                           monthly_carrying_pct: float) -> Optional[float]:
    """How long stock would have to sit before taking a discount today pays.

    The dead-stock analogue of ``break_even_volume_change`` above, and it earns
    its place for the same reason: it is the one thing this module can say
    about clearing a shelf that needs **no behavioural assumption at all**.

    Taking ``d`` off costs ``V·d``, once. Keeping the line costs ``V·m`` every
    month. They are equal at ``d / m`` months, and the ``V`` cancels — so the
    answer is a property of the discount and the carrying rate, not of the
    line, and one number serves the whole shelf.

    What it hands the owner is a *frontier*, not a verdict: at 12% a year a 25%
    discount is worth taking if the stock would otherwise have sat more than 25
    months. The platform supplies the arithmetic; whether this line would have
    sat that long is the owner's judgement, and it is the judgement the stock
    module refuses to make on their behalf (see ``insight/stock.py``, which
    declines recovery probability for exactly this reason).

    Two honest limits, both stated in the response rather than buried here:

    **It is first-order.** Discounting the cash flows properly lengthens the
    boundary — at a 10% cost of capital with 2% storage, a 25% discount breaks
    even nearer 28 months than 25. The linear rule is therefore *conservative*:
    it understates how long you can afford to wait, so it errs toward
    discounting too eagerly rather than too late.

    **The rate cannot be split.** A true NPV needs the cost-of-capital half
    (which comes *back* when stock is sold) separated from the storage and
    obsolescence half (which merely *stops*). ``carrying_cost_annual_pct`` is a
    single number and cannot express the difference.

    ``None`` when the carrying rate is zero or negative, where the question has
    no answer: stock that costs nothing to keep never has to be cleared.
    """
    if monthly_carrying_pct <= 0:
        return None
    return discount / monthly_carrying_pct


def inventory_change(lines: Iterable[ShelfLine], *, monthly_carrying_pct: float,
                     share: float = 1.0, discount: float = 0.0) -> dict:
    """What clearing some of the shelf returns, and what it stops costing.

    ``share`` is how much of each line moves; ``discount`` is what it takes to
    move it, as a fraction off the purchase cost. Both are the user's
    assumptions and neither is guessed at — the response names them and reports
    the result *at* them rather than as a projection.

    A line with no purchase rate is counted separately rather than valued at
    zero. Nobody has costed it; that is not the same as it being free, and
    folding it in at zero would understate what is on the shelf by exactly the
    lines nobody has looked at.
    """
    priced = [ln for ln in lines if ln.priced]
    unpriced = [ln for ln in lines if not ln.priced and ln.on_hand > 0]

    held = sum(ln.capital for ln in priced)
    moving = held * share
    # What comes back is the cost of what moves, less the discount it takes.
    # Never "what it would sell for": this module has no selling price for
    # stock nothing is currently selling, and inventing one is the whole class
    # of lie the module docstring refuses.
    released = moving * (1.0 - discount)
    written_off = moving - released
    remaining = held - moving

    rows = sorted(
        ({"product_id": ln.product_id, "label": ln.label,
          "on_hand": round(ln.on_hand, 2),
          "capital": round(ln.capital, 2),
          "moves": round(ln.capital * share, 2),
          "releases": round(ln.capital * share * (1.0 - discount), 2),
          "monthly_saved": round(ln.capital * share * monthly_carrying_pct, 2),
          "idle_days": ln.idle_days}
         for ln in priced),
        key=lambda r: -r["releases"])

    return {
        "scenario": INVENTORY_CHANGE,
        "assumptions": {
            "share_moved": share,
            "discount": discount,
            "note": ("Both are yours, not the platform's. The figures below are "
                     "what follows from them arithmetically — nothing here "
                     "predicts whether the stock will actually move."),
        },
        "capital_held": round(held, 2),
        "capital_released": round(released, 2),
        "written_off": round(written_off, 2),
        "capital_remaining": round(remaining, 2),
        # The recurring half, and the one a person underestimates: clearing the
        # shelf stops a payment that repeats every month for as long as the
        # stock sits.
        "monthly_carrying_saved": round(moving * monthly_carrying_pct, 2),
        "annual_carrying_saved": round(moving * monthly_carrying_pct * 12, 2),
        # The headline that needs no assumption about whether the stock moves —
        # the same role ``break_even_volume_change`` plays for a price change.
        "break_even_idle_months": (
            round(be, 1)
            if (be := break_even_idle_months(discount, monthly_carrying_pct))
            is not None else None),
        "break_even_note": (
            "Taking this discount today is worth it if the stock would "
            "otherwise have sat longer than this. That boundary needs no "
            "forecast — it is the discount divided by the monthly carrying "
            "rate. Whether this stock would have sat that long is your call, "
            "not the platform's. The figure is deliberately first-order: "
            "discounting the cash flows properly lengthens it, so it errs "
            "toward clearing too eagerly rather than too late."),
        "lines": rows[:100],
        "line_count": len(priced),
        "unpriced_lines": len(unpriced),
        "unpriced_note": (
            None if not unpriced else
            f"{len(unpriced)} line(s) on the shelf have no purchase cost "
            "recorded, so they are excluded rather than valued at zero. What "
            "they are worth is unknown, not nothing."),
    }


# ── supplier delay ───────────────────────────────────────────────────────────

@dataclass
class OpenCommitment:
    """What one supplier owes us, from folded COMMITMENTS state."""

    vendor_id: str
    label: str
    open_orders: int
    open_value: float
    oldest_open_on: Optional[str]
    payment_terms_days: Optional[int]


def supplier_delay(commitments: Iterable[OpenCommitment], *, days: int,
                   as_of: date) -> dict:
    """What slipping every open order by ``days`` moves, in cash and in age.

    Two effects, both mechanical:

    **Cash stays put.** An order that arrives later is a bill raised later, so
    the money committed to it is not owed for those extra days. Where the
    supplier has payment terms recorded, the delay pushes the payable that much
    further out; where they do not, the response says so instead of assuming
    a term.

    **Commitment ages.** An order already open for 90 days becomes one open for
    90 + ``days``. That is the number worth a phone call, and it is a fact
    rather than lateness — this book records no promised dates, so there is
    nothing to be late against.

    What this deliberately does not say is which items run short. Purchase
    orders are read at header grain, so nothing links a delayed order to a
    shelf. See ``UNAVAILABLE``.
    """
    rows = []
    total_value = 0.0
    without_terms = 0
    for c in commitments:
        if c.open_orders <= 0:
            continue
        total_value += c.open_value
        age = None
        if c.oldest_open_on:
            age = (as_of - date.fromisoformat(c.oldest_open_on)).days
        if c.payment_terms_days is None:
            without_terms += 1
        rows.append({
            "vendor_id": c.vendor_id, "label": c.label,
            "open_orders": c.open_orders,
            "open_value": round(c.open_value, 2),
            "oldest_age_days": age,
            "oldest_age_after": (age + days) if age is not None else None,
            "payment_terms_days": c.payment_terms_days,
            # When the money would have been due, and when it would be instead.
            # Null where the supplier has no terms recorded: a payable with no
            # term cannot be pushed out by a number nobody agreed.
            "payable_deferred_days": (days if c.payment_terms_days is not None
                                      else None),
        })
    rows.sort(key=lambda r: -r["open_value"])

    deferred = sum(r["open_value"] for r in rows
                   if r["payable_deferred_days"] is not None)
    return {
        "scenario": SUPPLIER_DELAY,
        "assumptions": {
            "delay_days": days,
            "note": ("Measured from today, not against a promised date — this "
                     "book records none. The result says how much later, not "
                     "how much late."),
        },
        "committed_value": round(total_value, 2),
        "cash_deferred": round(deferred, 2),
        "cash_deferred_days": days,
        "suppliers": rows[:100],
        "supplier_count": len(rows),
        "suppliers_without_terms": without_terms,
        "terms_note": (
            None if not without_terms else
            f"{without_terms} supplier(s) have no payment terms recorded, so "
            "the cash effect of delaying their orders cannot be dated. Their "
            "committed value is counted; the deferral is not."),
        "unavailable": [dict(u) for u in UNAVAILABLE],
    }
