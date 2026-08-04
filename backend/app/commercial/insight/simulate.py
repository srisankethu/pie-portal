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

Two of the four scenarios in the specification are not implemented, because the
data does not exist and building them would mean fabricating it:

  * **supplier delay** needs vendor lead times and open purchase orders
  * **inventory change** needs stock levels

Neither is in the read model. ``UNAVAILABLE`` names them with the reason, so the
screen can show them as blocked-on-data instead of silently offering three
options where the specification asked for five.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models

PRICE_CHANGE = "PRICE_CHANGE"
CUSTOMER_RECOVERY = "CUSTOMER_RECOVERY"
MARGIN_FLOOR = "MARGIN_FLOOR"

#: Named, with the missing input, so the UI can explain rather than omit.
UNAVAILABLE: tuple[dict[str, str], ...] = (
    {"scenario": "SUPPLIER_DELAY",
     "needs": "vendor lead times and open purchase orders",
     "why": "The read model holds bill lines, which say what was bought and at "
            "what cost, not what is on order or when it is due."},
    {"scenario": "INVENTORY_CHANGE",
     "needs": "stock on hand per item",
     "why": "Stock is not synced from Zoho into this platform, so any inventory "
            "figure here would be fabricated."},
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
