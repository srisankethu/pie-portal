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

**Neither does the boundary a rule fires at.** That is a later and harder
lesson than the one above. Stripping the reasoning from a rule leaves the fact
that it fired, and the caller supplies ``proposed_price`` — so walking the price
finds the exact value at which the rule changes its answer, and for the three
cost rules that value is computed from cost. Twenty probes recovered the floor
in the ``filterCounts.MFLOOR`` incident; here two hundred lines fit in one
request. ``boundary_refs`` records what each rule's boundary is made of, and
``quote_service.project`` withholds a rule from any role denied one of them.
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

#: Substituted for the cost-derived rules when the recipient may not see cost.
#: Not a rule anything here evaluates — see ``quote_service.project``.
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
REVIEW_EXPECTED = "REVIEW_EXPECTED"

#: A ``boundary_refs`` member naming purchase cost itself rather than one of the
#: ``PriceReference`` codes. ``NEGATIVE_MARGIN`` fires at ``price <= unit_cost``
#: with no policy multiplier in the comparison at all, so there is no reference
#: code that describes where its boundary sits.
COST_BASIS = "COST_BASIS"


@dataclass(frozen=True)
class QuoteException:
    """One rule that fired on one quote line."""

    code: str
    severity: str
    title: str
    detail: str                              # salesperson-safe: no cost, no margin
    manager_detail: Optional[str] = None     # may name cost and margin
    impact_amount: Optional[Decimal] = None
    impact_data_class: str = OPERATIONAL
    reference_code: Optional[str] = None
    requires_approval: bool = False
    # A stated policy boundary rather than an advisory comparison. Policy
    # exceptions are never suppressed for being small: the business asked for
    # these lines to be looked at, and "it was only ₹165 under" is a judgement
    # for the reviewer to make, not for the engine to make on their behalf.
    policy: bool = False
    inputs: dict = field(default_factory=dict)   # the exact numbers compared
    #: The values that decide **where this rule's boundary sits** — not every
    #: value its condition reads. The distinction is the whole design:
    #:
    #: A caller who supplies ``proposed_price`` can walk it until the rule
    #: changes its answer, and what they learn is the boundary. So a rule
    #: discloses precisely the numbers that *place* that boundary, and a role
    #: denied any of them must not receive the rule.
    #:
    #: A condition that only *arms* a rule from history contributes nothing:
    #: ``COST_INCREASE_NOT_PASSED`` reads a past cost movement to decide whether
    #: to look at all, but it fires at this customer's last price — a number the
    #: salesperson is shown. It lists ``LAST_PRICE_PAID`` and not ``COST_BASIS``,
    #: and that is not an oversight. Listing every input read would withhold a
    #: rule the negotiation desk needs while protecting nothing, which is the
    #: failure mode CLAUDE.md §1 warns against in its first corollary.
    #:
    #: A rule with no price in its condition cannot be walked and carries an
    #: empty set.
    boundary_refs: frozenset[str] = frozenset()

    def to_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity, "title": self.title,
            "detail": self.detail, "manager_detail": self.manager_detail,
            "impact_amount": (float(round(self.impact_amount, 2))
                              if self.impact_amount is not None else None),
            "impact_data_class": self.impact_data_class,
            "reference_code": self.reference_code,
            "requires_approval": self.requires_approval,
            "policy": self.policy,
            "inputs": self.inputs,
            # Sorted list rather than a set: this dict is persisted in
            # ``quote_decisions`` and read back by ``snapshot_to_dict``, so it
            # has to survive JSON and compare stably.
            "boundary_refs": sorted(self.boundary_refs),
        }


def _pct(ratio: Optional[float]) -> str:
    return f"{ratio * 100:.1f}%" if ratio is not None else "unknown"


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
        # Empty ``boundary_refs`` on purpose. This states that we hold no cost
        # for the item, which is a fact about our own master rather than a
        # boundary — it is the same at every price, and ``references_withheld``
        # tells the same reader the same thing. It stays visible because a
        # salesperson pricing an item nobody has costed needs to know that.
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
                manager_detail=(f"Quoted {th.money(price)} against an effective "
                                f"unit cost of {th.money(unit_cost)} — "
                                f"{_pct(margin)} margin on {_qty(qty)} units."),
                impact_amount=to_floor, impact_data_class=RESTRICTED,
                reference_code=MIN_MARGIN_PRICE, requires_approval=True,
                policy=True,
                # Cost itself, with no multiplier in the comparison — which is
                # why this one is the sharpest of the three. Finding where it
                # switches on is finding the purchase price to the paisa, and
                # needs no knowledge of any policy parameter to interpret.
                boundary_refs=frozenset({COST_BASIS}),
                inputs={"quoted": float(price), "qty": float(qty)}))
        elif margin is not None and margin < th.min_margin:
            found.append(QuoteException(
                code=BELOW_MIN_MARGIN, severity=CRITICAL,
                title="Below the approval floor for this item",
                detail=("This price is below the minimum this item may be sold "
                        "at without approval."),
                manager_detail=(f"Margin {_pct(margin)} against a "
                                f"{_pct(th.min_margin)} hard minimum — "
                                f"{th.money(to_floor)} short across "
                                f"{_qty(qty)} units."),
                impact_amount=to_floor, impact_data_class=RESTRICTED,
                reference_code=MIN_MARGIN_PRICE, requires_approval=True,
                policy=True,
                boundary_refs=frozenset({COST_BASIS, MIN_MARGIN_PRICE}),
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
                                f"{th.money(line_value)} line — "
                                f"{th.money(to_review)} short."),
                impact_amount=to_review, impact_data_class=RESTRICTED,
                reference_code=MARGIN_FLOOR_PRICE, policy=True,
                boundary_refs=frozenset({COST_BASIS, MARGIN_FLOOR_PRICE}),
                inputs={"quoted": float(price), "qty": float(qty)}))

    # ── this customer's own past prices ─────────────────────────────────────
    last = refs.get(LAST_PRICE_PAID)
    if last is not None and price < last.value * (Decimal("1") - tol):
        gap = _shortfall(last.value, price, qty)
        found.append(QuoteException(
            code=BELOW_LAST_PRICE, severity=WARNING,
            title="Below what this customer last paid",
            detail=(f"They paid {th.money(last.value)} on "
                    f"{last.as_of.isoformat() if last.as_of else 'the last order'}. "
                    f"At {th.money(price)} you are giving back "
                    f"{th.money(gap)} across {_qty(qty)} units — deliberate is "
                    f"fine, accidental is not."),
            impact_amount=gap, impact_data_class=OPERATIONAL,
            reference_code=LAST_PRICE_PAID,
            boundary_refs=frozenset({LAST_PRICE_PAID}),
            inputs={"quoted": float(price), "reference": float(last.value),
                    "qty": float(qty)}))

    band = refs.get(BAND_PRICE)
    if band is not None and price < band.value * (Decimal("1") - tol):
        gap = _shortfall(band.value, price, qty)
        found.append(QuoteException(
            code=BELOW_BAND_PRICE, severity=WARNING,
            title=f"Below their usual price at {band.qty_band} units",
            detail=(f"Across {band.txn_count} past orders of this size they have "
                    f"paid about {th.money(band.value)}. This quote is "
                    f"{th.money(gap)} below that on the line."),
            impact_amount=gap, impact_data_class=OPERATIONAL,
            reference_code=BAND_PRICE,
            boundary_refs=frozenset({BAND_PRICE}),
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
                                f"is {th.money(peer.value)}; this quote is "
                                f"{th.money(gap)} below it on the line."),
                impact_amount=gap, impact_data_class=RESTRICTED,
                reference_code=PEER_MEDIAN_PRICE,
                # The pair below and above the median bracket it from both
                # sides, and the tolerance that sets the bracket width is
                # published to every role by ``/quote-intelligence/thresholds``.
                # So the two together invert to the median exactly — a value
                # ``build_references`` classifies RESTRICTED and ``project``
                # strips by name.
                boundary_refs=frozenset({PEER_MEDIAN_PRICE}),
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
                                f"is {th.money(peer.value)}; this quote is "
                                f"{th.money(over)} above it on the line."),
                impact_amount=over, impact_data_class=RESTRICTED,
                reference_code=PEER_MEDIAN_PRICE,
                boundary_refs=frozenset({PEER_MEDIAN_PRICE}),
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
                # ``LAST_PRICE_PAID`` and deliberately not ``COST_BASIS``. The
                # cost movement decides whether this rule is armed; it does not
                # place the boundary. What moving the price crosses is this
                # customer's own last price, which the salesperson is shown.
                # Listing the cost here would withhold the one warning that
                # tells a salesperson our buying price moved and theirs did not
                # — blunting the desk to protect nothing.
                boundary_refs=frozenset({LAST_PRICE_PAID}),
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
                impact_amount=metrics.historical_margin_gap,
                impact_data_class=RESTRICTED,
                # No ``boundary_refs``, and that is the correct empty set rather
                # than an omission: this rule's condition contains no price, so
                # there is nothing for a caller to walk. It says the same thing
                # at every price. Its rupee ``impact_amount`` is RESTRICTED and
                # ``project`` already drops that.
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
    floor = Decimal(str(th.min_quote_exception_impact))
    kept = [
        e for e in found
        if e.severity == CRITICAL or e.policy
        or e.impact_amount is None or e.impact_amount >= floor
    ]
    return sorted(
        kept,
        key=lambda e: (_SEVERITY_RANK.get(e.severity, 9),
                       -float(e.impact_amount or 0),
                       e.code),
    )


def _qty(q: Decimal) -> str:
    return f"{q:.0f}" if q == q.to_integral_value() else f"{q}"
