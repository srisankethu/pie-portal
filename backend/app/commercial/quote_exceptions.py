"""The deterministic quote exception engine.

Given a proposed price, a quantity, a cost basis and this relationship's
history, which of a fixed set of rules fire — and in what order does a human
need to read them?

Every rule is a total function of numbers computed elsewhere in this package.
There is no model call here and there must never be one: an exception that
appears on Tuesday and not on Wednesday for identical inputs is not a control,
it is noise, and a salesperson learns within a week to ignore it.

Three deliberate choices:

**Ranking is by money, then severity — not by percentage.** A 2 pp shortfall on
a ₹9,00,000 line outranks a 15 pp shortfall on a ₹4,000 one, and a screen sorted
by percentage puts them the other way round.

**Small advisory exceptions are suppressed; policy breaches never are.** Below
the impact floor, "you are ₹40 under their last price" is noise. But a price
under cost, under the approval floor, or under the review floor is a stated
boundary of the business, and it is reported at any size — a control that
quietly disappears on small lines is a control nobody trusts on large ones.

**Cost figures do not travel.** Each exception carries a salesperson-safe
``detail`` and, separately, a ``manager_detail`` that may name cost and margin.
The API drops the latter for a sales role — absent, not masked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency
from .benchmark import ItemBenchmark
from .config import CommercialThresholds
from .metrics import COST_DRIVEN, MIXED, RelationshipMetrics
from .references import (
    BAND_PRICE,
    LAST_PRICE_PAID,
    MARGIN_FLOOR_PRICE,
    MIN_MARGIN_PRICE,
    OPERATIONAL,
    PEER_MEDIAN_PRICE,
    PriceReference,
    RESTRICTED,
    by_code,
)

_ZERO = Decimal("0")

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

_SEVERITY_RANK = {CRITICAL: 0, WARNING: 1, INFO: 2}

# Stable codes — persisted in quote decision snapshots.
NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
BELOW_MIN_MARGIN = "BELOW_MIN_MARGIN"
BELOW_MARGIN_FLOOR = "BELOW_MARGIN_FLOOR"
BELOW_LAST_PRICE = "BELOW_LAST_PRICE"
BELOW_BAND_PRICE = "BELOW_BAND_PRICE"
BELOW_PEER_MEDIAN = "BELOW_PEER_MEDIAN"
ABOVE_PEER_MEDIAN = "ABOVE_PEER_MEDIAN"
COST_INCREASE_NOT_PASSED = "COST_INCREASE_NOT_PASSED"
MARGIN_EROSION = "MARGIN_EROSION"
NO_COST_BASIS = "NO_COST_BASIS"
NO_PRICE_SET = "NO_PRICE_SET"
NEW_RELATIONSHIP = "NEW_RELATIONSHIP"
THIN_HISTORY = "THIN_HISTORY"


@dataclass(frozen=True)
class QuoteException:
    """One rule that fired on one quote line."""

    code: str
    severity: str
    title: str
    detail: str                              # salesperson-safe: no cost, no margin
    manager_detail: Optional[str] = None     # may name cost and margin
    impact_rupees: Optional[Decimal] = None
    impact_data_class: str = OPERATIONAL
    reference_code: Optional[str] = None
    requires_approval: bool = False
    # A stated policy boundary rather than an advisory comparison. Policy
    # exceptions are never suppressed for being small: the business asked for
    # these lines to be looked at, and "it was only ₹165 under" is a judgement
    # for the reviewer to make, not for the engine to make on their behalf.
    policy: bool = False
    inputs: dict = field(default_factory=dict)   # the exact numbers compared

    def to_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity, "title": self.title,
            "detail": self.detail, "manager_detail": self.manager_detail,
            "impact_rupees": (float(round(self.impact_rupees, 2))
                              if self.impact_rupees is not None else None),
            "impact_data_class": self.impact_data_class,
            "reference_code": self.reference_code,
            "requires_approval": self.requires_approval,
            "policy": self.policy,
            "inputs": self.inputs,
        }


def _pct(ratio: Optional[float]) -> str:
    return f"{ratio * 100:.1f}%" if ratio is not None else "unknown"


def _rupees(amount: Optional[Decimal]) -> str:
    return f"₹{float(amount):,.0f}" if amount is not None else "unknown"


def _shortfall(reference: Decimal, proposed: Decimal, qty: Decimal) -> Decimal:
    return max(_ZERO, (reference - proposed) * qty)


def evaluate(
    *,
    proposed_price: Optional[Decimal],
    qty: Decimal,
    unit_cost: Optional[Decimal],
    references: list[PriceReference],
    metrics: Optional[RelationshipMetrics],
    benchmark: Optional[ItemBenchmark],
    th: CommercialThresholds,
) -> list[QuoteException]:
    """Run every rule, then rank. Pure; identical inputs give an identical list."""
    refs = by_code(references)
    found: list[QuoteException] = []
    tol = Decimal(str(th.quote_price_tolerance_pct))
    has_history = bool(metrics and metrics.transaction_count)
    # Trend claims ("cost rose", "margin eroded") need enough evidence to be
    # claims at all. Single observed facts ("they paid ₹X on this date") do not.
    trends_usable = bool(
        metrics and metrics.data_sufficiency is not EvidenceSufficiency.INSUFFICIENT)

    # ── data-quality rules ──────────────────────────────────────────────────
    if not has_history:
        found.append(QuoteException(
            code=NEW_RELATIONSHIP, severity=INFO,
            title="First time for this customer and item",
            detail=("This customer has not bought this item before, so there is "
                    "no prior price to hold to. Price it on its merits.")))
    elif not trends_usable:
        reasons = "; ".join(metrics.sufficiency_reasons) if metrics else ""
        found.append(QuoteException(
            code=THIN_HISTORY, severity=INFO,
            title="Thin history — treat comparisons carefully",
            detail=(f"There is not enough trading history to read a trend"
                    f"{f' — {reasons}' if reasons else ''}. Individual past "
                    f"prices below are still exactly what was invoiced.")))

    if unit_cost is None:
        found.append(QuoteException(
            code=NO_COST_BASIS, severity=INFO,
            title="No purchase cost on record",
            detail=("No purchase cost is recorded for this item as of today, so "
                    "profitability cannot be checked on this line. The price "
                    "comparisons below are unaffected.")))

    if proposed_price is None:
        found.append(QuoteException(
            code=NO_PRICE_SET, severity=INFO, title="No price set yet",
            detail="Enter a quoted price to check it against the references below."))
        return _rank(found, th)

    price = proposed_price
    line_value = price * qty

    # ── cost-based rules ────────────────────────────────────────────────────
    # Valid regardless of how thin the sales history is: they depend on today's
    # cost and today's price, not on a trend.
    if unit_cost is not None and unit_cost > _ZERO:
        margin = float((price - unit_cost) / price) if price > _ZERO else None
        floor_ref = refs.get(MIN_MARGIN_PRICE)
        to_floor = (_shortfall(floor_ref.value, price, qty)
                    if floor_ref is not None else None)

        if price <= unit_cost:
            found.append(QuoteException(
                code=NEGATIVE_MARGIN, severity=CRITICAL,
                title="This price does not cover what the item costs us",
                detail=("At this price the line loses money on every unit. It "
                        "needs approval before it can go out."),
                manager_detail=(f"Quoted {_rupees(price)} against an effective "
                                f"unit cost of {_rupees(unit_cost)} — "
                                f"{_pct(margin)} margin on {_qty(qty)} units."),
                impact_rupees=to_floor, impact_data_class=RESTRICTED,
                reference_code=MIN_MARGIN_PRICE, requires_approval=True,
                policy=True,
                inputs={"quoted": float(price), "qty": float(qty)}))
        elif margin is not None and margin < th.min_margin:
            found.append(QuoteException(
                code=BELOW_MIN_MARGIN, severity=CRITICAL,
                title="Below the approval floor for this item",
                detail=("This price is below the minimum this item may be sold "
                        "at without approval."),
                manager_detail=(f"Margin {_pct(margin)} against a "
                                f"{_pct(th.min_margin)} hard minimum — "
                                f"{_rupees(to_floor)} short across "
                                f"{_qty(qty)} units."),
                impact_rupees=to_floor, impact_data_class=RESTRICTED,
                reference_code=MIN_MARGIN_PRICE, requires_approval=True,
                policy=True,
                inputs={"quoted": float(price), "qty": float(qty)}))
        elif margin is not None and margin < th.margin_floor:
            # Measured against the *review* floor, not the approval floor —
            # against the latter the gap is zero by construction (the price
            # already clears it), and the exception would be suppressed as
            # immaterial every single time.
            review_ref = refs.get(MARGIN_FLOOR_PRICE)
            to_review = (_shortfall(review_ref.value, price, qty)
                         if review_ref is not None else None)
            found.append(QuoteException(
                code=BELOW_MARGIN_FLOOR, severity=WARNING,
                title="Thin on this line",
                detail=("This price is below the level the business normally "
                        "reviews. It can go out, but it will be looked at."),
                manager_detail=(f"Margin {_pct(margin)} against a "
                                f"{_pct(th.margin_floor)} review floor on a "
                                f"{_rupees(line_value)} line — "
                                f"{_rupees(to_review)} short."),
                impact_rupees=to_review, impact_data_class=RESTRICTED,
                reference_code=MARGIN_FLOOR_PRICE, policy=True,
                inputs={"quoted": float(price), "qty": float(qty)}))

    # ── this customer's own past prices ─────────────────────────────────────
    last = refs.get(LAST_PRICE_PAID)
    if last is not None and price < last.value * (Decimal("1") - tol):
        gap = _shortfall(last.value, price, qty)
        found.append(QuoteException(
            code=BELOW_LAST_PRICE, severity=WARNING,
            title="Below what this customer last paid",
            detail=(f"They paid {_rupees(last.value)} on "
                    f"{last.as_of.isoformat() if last.as_of else 'the last order'}. "
                    f"At {_rupees(price)} you are giving back "
                    f"{_rupees(gap)} across {_qty(qty)} units — deliberate is "
                    f"fine, accidental is not."),
            impact_rupees=gap, impact_data_class=OPERATIONAL,
            reference_code=LAST_PRICE_PAID,
            inputs={"quoted": float(price), "reference": float(last.value),
                    "qty": float(qty)}))

    band = refs.get(BAND_PRICE)
    if band is not None and price < band.value * (Decimal("1") - tol):
        gap = _shortfall(band.value, price, qty)
        found.append(QuoteException(
            code=BELOW_BAND_PRICE, severity=WARNING,
            title=f"Below their usual price at {band.qty_band} units",
            detail=(f"Across {band.txn_count} past orders of this size they have "
                    f"paid about {_rupees(band.value)}. This quote is "
                    f"{_rupees(gap)} below that on the line."),
            impact_rupees=gap, impact_data_class=OPERATIONAL,
            reference_code=BAND_PRICE,
            inputs={"quoted": float(price), "reference": float(band.value),
                    "qty": float(qty), "band": band.qty_band}))

    # ── against the rest of the book ────────────────────────────────────────
    peer = refs.get(PEER_MEDIAN_PRICE)
    if peer is not None:
        if price < peer.value * (Decimal("1") - tol):
            gap = _shortfall(peer.value, price, qty)
            found.append(QuoteException(
                code=BELOW_PEER_MEDIAN, severity=WARNING,
                title="Below what comparable customers pay",
                detail=("Other customers buying this item pay more. That may be "
                        "right for this account — volume, terms and freight "
                        "differ — but it is worth knowing before you send it."),
                manager_detail=(f"Median across {peer.txn_count} other customers "
                                f"is {_rupees(peer.value)}; this quote is "
                                f"{_rupees(gap)} below it on the line."),
                impact_rupees=gap, impact_data_class=RESTRICTED,
                reference_code=PEER_MEDIAN_PRICE,
                inputs={"quoted": float(price), "reference": float(peer.value),
                        "qty": float(qty)}))
        elif price > peer.value * (Decimal("1") + tol):
            over = (price - peer.value) * qty
            found.append(QuoteException(
                code=ABOVE_PEER_MEDIAN, severity=INFO,
                title="Above what comparable customers pay",
                detail=("This price sits above the rest of the book for this "
                        "item. Defensible on service or terms — but expect it "
                        "to be challenged."),
                manager_detail=(f"Median across {peer.txn_count} other customers "
                                f"is {_rupees(peer.value)}; this quote is "
                                f"{_rupees(over)} above it on the line."),
                impact_rupees=over, impact_data_class=RESTRICTED,
                reference_code=PEER_MEDIAN_PRICE,
                inputs={"quoted": float(price), "reference": float(peer.value),
                        "qty": float(qty)}))

    # ── trend rules ─────────────────────────────────────────────────────────
    if trends_usable and metrics is not None:
        cost_up = (metrics.cost_change_pct is not None
                   and metrics.cost_change_pct >= th.meaningful_cost_increase_pct)
        if cost_up and last is not None and price <= last.value * (Decimal("1") + tol):
            found.append(QuoteException(
                code=COST_INCREASE_NOT_PASSED, severity=WARNING,
                title="What we pay for this has gone up — this price has not",
                detail=("Our buying price for this item has risen since these "
                        "orders were placed, and this quote holds the old level. "
                        "If that is intentional, say so; if not, this is where "
                        "margin leaves quietly."),
                manager_detail=(f"Effective unit cost up "
                                f"{_pct(metrics.cost_change_pct)} while the "
                                f"selling price moved "
                                f"{_pct(metrics.price_change_pct)} "
                                f"({metrics.erosion_kind})."),
                reference_code=LAST_PRICE_PAID,
                inputs={"cost_change_pct": metrics.cost_change_pct,
                        "price_change_pct": metrics.price_change_pct}))

        if (metrics.margin_change_pp is not None
                and metrics.margin_change_pp <= -th.min_margin_deterioration_pp):
            found.append(QuoteException(
                code=MARGIN_EROSION, severity=INFO,
                title="This item has been getting less profitable here",
                detail=("Profitability on this item with this customer has been "
                        "sliding for a while. This quote is a chance to correct "
                        "it, or a decision to accept it."),
                manager_detail=(
                    f"Margin {_pct(metrics.historical_margin)} → "
                    f"{_pct(metrics.current_margin)} "
                    f"({abs(metrics.margin_change_pp) * 100:.1f} pp), "
                    f"{metrics.erosion_kind.lower().replace('_', '-')}"
                    if metrics.erosion_kind in (COST_DRIVEN, MIXED)
                    else f"Margin {_pct(metrics.historical_margin)} → "
                         f"{_pct(metrics.current_margin)} "
                         f"({abs(metrics.margin_change_pp) * 100:.1f} pp)"),
                impact_rupees=metrics.historical_margin_gap,
                impact_data_class=RESTRICTED,
                inputs={"margin_change_pp": metrics.margin_change_pp}))

    return _rank(found, th)


def _rank(found: list[QuoteException], th: CommercialThresholds) -> list[QuoteException]:
    """Drop immaterial non-critical exceptions, then order by severity and money.

    The impact floor applies only to advisory comparisons, and only where an
    impact was actually computed. A stated policy boundary (below cost, below
    the approval floor, below the review floor) is never dropped for being
    small, and neither is a qualitative flag with no rupee figure at all — a
    control that silently disappears below some size is not a control.
    """
    floor = Decimal(str(th.min_quote_exception_impact_rupees))
    kept = [
        e for e in found
        if e.severity == CRITICAL or e.policy
        or e.impact_rupees is None or e.impact_rupees >= floor
    ]
    return sorted(
        kept,
        key=lambda e: (_SEVERITY_RANK.get(e.severity, 9),
                       -float(e.impact_rupees or 0),
                       e.code),
    )


def _qty(q: Decimal) -> str:
    return f"{q:.0f}" if q == q.to_integral_value() else f"{q}"
